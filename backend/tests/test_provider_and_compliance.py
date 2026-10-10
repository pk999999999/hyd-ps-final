import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.models.models import Lead, Communication, SendLog, ComplianceDecision, ContactPermission
from app.services.model_provider import ProviderError, generate_text, generate_structured, selected_provider
from app.services.email_service import EmailService
from app.services.email_provider import ResendEmailProvider
from app.services.communication_service import suppress_contact
from app.services.compliance_service import evaluate_outbound
from conftest import signup


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,settings_kwargs,response,expected_path", [
    ("openai", {"openai_api_key": "key", "openai_model": "test-model"},
     {"choices": [{"message": {"content": "OpenAI answer"}}]}, "/v1/chat/completions"),
    ("anthropic", {"anthropic_api_key": "key", "anthropic_model": "test-model"},
     {"content": [{"type": "text", "text": "Anthropic answer"}]}, "/v1/messages"),
    ("groq", {"groq_api_key": "key", "groq_model": "test-model"},
     {"choices": [{"message": {"content": "Groq answer"}}]}, "/openai/v1/chat/completions"),
    ("gemini", {"gemini_api_key": "key", "gemini_model": "test-model"},
     {"candidates": [{"content": {"parts": [{"text": "Gemini answer"}]}}]}, "/v1beta/models/test-model:generateContent"),
    ("compatible", {"llm_base_url": "http://local.test/v1", "llm_model": "test-model"},
     {"choices": [{"message": {"content": "Local answer"}}]}, "/v1/chat/completions"),
])
async def test_provider_adapters_use_explicit_selected_endpoint(monkeypatch, provider, settings_kwargs, response, expected_path):
    settings = Settings(_env_file=None, llm_provider=provider, **settings_kwargs)
    calls = []
    original = httpx.AsyncClient
    def remote(request):
        calls.append(request)
        return httpx.Response(200, json=response)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(remote), **kwargs))
    assert (await generate_text("System", "User", settings=settings)).endswith("answer")
    assert calls[0].url.path == expected_path
    assert b"test-model" in calls[0].content or "test-model" in calls[0].url.path
    if provider == "anthropic":
        assert calls[0].headers["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
async def test_provider_never_falls_through_after_selected_failure(monkeypatch):
    settings = Settings(_env_file=None, llm_provider="auto", openrouter_api_key="first",
                        groq_api_key="second")
    assert selected_provider(settings) == "openrouter"
    calls = []
    original = httpx.AsyncClient
    def remote(request):
        calls.append(request.url.host)
        return httpx.Response(429, json={"error": "rate limit"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(remote), **kwargs))
    with pytest.raises(ProviderError, match="rate limit"):
        await generate_text("system", "user", settings=settings)
    assert calls == ["openrouter.ai"]


@pytest.mark.asyncio
async def test_structured_output_is_validated(monkeypatch):
    from pydantic import BaseModel
    class Output(BaseModel):
        ideas: list[str]
    from app.services import model_provider
    monkeypatch.setattr(model_provider, "generate_text", AsyncMock(return_value='{"ideas":["one"]}'))
    assert (await generate_structured("system", "user", Output)).ideas == ["one"]
    monkeypatch.setattr(model_provider, "generate_text", AsyncMock(return_value='{"ideas":"not a list"}'))
    with pytest.raises(ProviderError, match="structured output"):
        await generate_structured("system", "user", Output)


@pytest.mark.asyncio
async def test_live_send_requires_permission_and_audits_both_decisions(harness, monkeypatch):
    client, factory, _ = harness
    auth, headers = await signup(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "email_provider", "resend")
    monkeypatch.setattr(settings, "email_enabled", True)
    monkeypatch.setattr(settings, "resend_api_key", "test-key")
    delivered = AsyncMock(return_value="provider-id")
    monkeypatch.setattr(ResendEmailProvider, "send", delivered)
    async with factory() as db:
        lead = Lead(user_id=uuid.UUID(auth["user"]["id"]), title="Salon", emails="lead@example.com",
                    outreach_status="approved", draft_subject="Hello", draft_body="A specific, reviewed message")
        db.add(lead)
        await db.commit()
        await db.refresh(lead)
        ok, detail = await EmailService(db).send_outreach_email(lead.user_id, lead, "Hello", "Hello", lead.draft_body)
        assert not ok and "permission_not_recorded" in detail
        assert delivered.await_count == 0
    assert (await client.post(f"/api/v1/leads/{lead.id}/permission", headers=headers,
                              json={"basis": "Existing client asked us to send this proposal"})).status_code == 200
    async with factory() as db:
        lead = await db.get(Lead, lead.id)
        assert (await EmailService(db).send_outreach_email(lead.user_id, lead, "Hello", "Hello", lead.draft_body))[0]
        decisions = (await db.scalars(select(ComplianceDecision).order_by(ComplianceDecision.checked_at))).all()
        assert [row.decision for row in decisions] == ["block", "allow"]
        assert delivered.await_count == 1
        await suppress_contact(db, lead, "opted_out")
        permission = await db.scalar(select(ContactPermission).where(ContactPermission.lead_id == lead.id))
        assert permission.revoked_at is not None


@pytest.mark.asyncio
async def test_drafted_reply_cannot_bypass_human_approval(harness, monkeypatch):
    client, factory, _ = harness
    monkeypatch.setattr(get_settings(), "email_provider", "smtp")
    auth, _ = await signup(client)
    user_id = uuid.UUID(auth["user"]["id"])
    async with factory() as db:
        lead = Lead(user_id=user_id, title="Clinic", emails="clinic@example.com")
        db.add(lead)
        await db.flush()
        message = Communication(user_id=user_id, lead_id=lead.id, direction="inbound",
            channel="email", status="drafted", event_key="drafted-reply-test")
        db.add(message)
        await db.flush()
        blocked = await evaluate_outbound(db, user_id, lead, channel="email", message_type="reply",
            subject="Re: appointment", body="A reviewed reply", message_id=message.id)
        assert "human_approval_required" in blocked.reason_codes
        message.status = "approved"
        allowed = await evaluate_outbound(db, user_id, lead, channel="email", message_type="reply",
            subject="Re: appointment", body="A reviewed reply", message_id=message.id)
        assert allowed.decision == "allow", allowed.reason_codes


@pytest.mark.asyncio
async def test_reply_rate_excludes_unsent_conversations(harness):
    client, factory, _ = harness
    auth, headers = await signup(client)
    user_id = uuid.UUID(auth["user"]["id"])
    async with factory() as db:
        sent_lead = Lead(user_id=user_id, title="Sent lead", emails="sent@example.com")
        unsent_lead = Lead(user_id=user_id, title="Portal only", emails="portal@example.com")
        db.add_all([sent_lead, unsent_lead])
        await db.flush()
        db.add(SendLog(user_id=user_id, lead_id=sent_lead.id, channel="email", recipient=sent_lead.email,
            message_type="outreach", status="sent", idempotency_key="analytics-sent"))
        for lead in (sent_lead, unsent_lead):
            db.add(Communication(user_id=user_id, lead_id=lead.id, direction="inbound", channel="email",
                classification="interested", event_key=f"analytics-{lead.id}"))
        await db.commit()
    response = await client.get("/api/v1/analytics", headers=headers)
    assert response.status_code == 200
    assert response.json()["rates"]["reply_rate"] == {"numerator": 1, "denominator": 1, "percent": 100.0}
    assert response.json()["rates"]["positive_response_rate"] == {"numerator": 1, "denominator": 1, "percent": 100.0}
