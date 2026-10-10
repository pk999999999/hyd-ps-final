from unittest.mock import AsyncMock
import uuid

import pytest
from sqlalchemy import select

from app import worker
from app.core.config import get_settings
from app.core.config import Settings
from app.models.models import Lead, SendLog
from app.services.email_provider import MailHogEmailProvider
from app.agents.scout_agent import call_llm, ProviderError
from conftest import signup
from pydantic import ValidationError


def test_demo_mode_requires_isolated_databases_and_local_mail():
    valid = {"_env_file": None, "demo_mode": True, "app_env": "demo",
             "database_url": "postgresql+asyncpg://user:pass@localhost/app_demo",
             "mongo_db": "memory_demo", "email_provider": "smtp", "email_enabled": False}
    assert Settings(**valid).demo_mode is True
    with pytest.raises(ValidationError, match="MailHog-only"):
        Settings(**{**valid, "email_provider": "resend"})
    with pytest.raises(ValidationError, match="separate"):
        Settings(**{**valid, "database_url": "postgresql+asyncpg://user:pass@localhost/live"})


@pytest.mark.asyncio
async def test_demo_mode_never_calls_live_reasoning(monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_mode", True)
    with pytest.raises(ProviderError, match="disabled in demo mode"):
        await call_llm("system", "user", strict=True)


@pytest.mark.asyncio
async def test_offline_demo_campaign_review_delivery_reply_and_opt_out(harness, monkeypatch):
    client, factory, _ = harness
    settings = get_settings()
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "app_env", "demo")
    monkeypatch.setattr(settings, "email_provider", "smtp")
    monkeypatch.setattr(settings, "email_enabled", False)
    delivery = AsyncMock(return_value="dry-run:demo-campaign")
    monkeypatch.setattr(MailHogEmailProvider, "send", delivery)

    _, headers = await signup(client)
    created = await client.post("/api/v1/campaigns", headers=headers, json={
        "service_description": "We improve appointment booking with AI",
        "target_clients": "Dental clinics in Mumbai, India",
    })
    assert created.status_code == 202, created.text

    for _ in range(4):
        claims = await worker.claim_work(20)
        if not claims:
            break
        for claim in claims:
            await worker.process_work(claim)

    campaigns = (await client.get("/api/v1/campaigns", headers=headers)).json()
    assert campaigns[0]["plan"]["demo"] is True
    assert campaigns[0]["plan"]["city"] == "Mumbai"
    leads = (await client.get("/api/v1/leads", headers=headers)).json()
    assert len(leads) == 3
    assert all(lead["title"].startswith("[Demo]") and lead["outreach_status"] == "drafted" for lead in leads)
    assert all("hypothesis" in lead["draft_body"].lower() for lead in leads)

    lead_id = leads[0]["id"]
    research = await client.get(f"/api/v1/leads/{lead_id}/research", headers=headers)
    assert research.status_code == 200
    assert research.json()["score"] > 0
    assert research.json()["evidence"][0]["confidence"] == "synthetic"
    assert research.json()["ideas"]
    detail = await client.get(f"/api/v1/campaigns/{campaigns[0]['id']}", headers=headers)
    assert detail.status_code == 200
    assert {step["kind"] for step in detail.json()["timeline"]} >= {"campaign_plan", "scrape", "draft"}
    analytics = await client.get("/api/v1/analytics", headers=headers)
    assert analytics.status_code == 200 and analytics.json()["evidence"] >= 3
    assert analytics.json()["research_completed"] == 3
    assert analytics.json()["rates"]["research_coverage"] == {"numerator": 3, "denominator": 3, "percent": 100.0}
    ideas = await client.get("/api/v1/ideas", headers=headers)
    assert ideas.status_code == 200 and len(ideas.json()) == 3
    assert all(idea["confidence"] == "hypothesis" for idea in ideas.json())
    settings_overview = await client.get("/api/v1/settings/overview", headers=headers)
    assert settings_overview.status_code == 200 and settings_overview.json()["demo_mode"] is True
    approved = await client.post(f"/api/v1/leads/{lead_id}/decision", headers=headers,
                                 json={"action": "approved", "send_email": True})
    assert approved.status_code == 200, approved.text
    assert delivery.await_count == 1
    async with factory() as db:
        sent = await db.scalar(select(SendLog).where(SendLog.lead_id == uuid.UUID(lead_id)))
        assert sent.status == "sent" and sent.resend_id == "dry-run:demo-campaign"

    routing = (await client.get(f"/api/v1/communications/simulation-address/{lead_id}", headers=headers)).json()
    payload = {"type": "email.received", "data": {"email_id": "demo-interest-1", "from": routing["from"],
        "to": [routing["to"]], "subject": "Re: demo", "text": "Interested. Can we schedule a demo?"}}
    received = await client.post("/api/v1/communications/simulate-reply", headers=headers, json=payload)
    assert received.status_code == 202, received.text
    for claim in await worker.claim_work(20):
        await worker.process_work(claim)
    interested = (await client.get("/api/v1/communications/interested", headers=headers)).json()
    assert len(interested) == 1 and interested[0]["draft_response"]

    payload["data"].update(email_id="demo-stop-1", text="STOP")
    assert (await client.post("/api/v1/communications/simulate-reply", headers=headers, json=payload)).status_code == 202
    for claim in await worker.claim_work(20):
        await worker.process_work(claim)
    async with factory() as db:
        assert (await db.get(Lead, uuid.UUID(lead_id))).opted_out is True
    assert (await client.get("/api/v1/communications/interested", headers=headers)).json() == []
