"""Deterministic, auditable gates for outbound delivery."""
from datetime import datetime, timezone
from pydantic import BaseModel
from sqlalchemy import select, func

from app.core.config import get_settings
from app.models.models import (Campaign, Communication, ComplianceDecision,
    ContactPermission, Job, Lead, SendLog)
from app.services.communication_service import is_suppressed, normalize_phone


POLICY_VERSION = "2026-10-v1"


class ComplianceResult(BaseModel):
    decision: str
    reason_codes: list[str]
    checked_at: datetime
    policy_version: str = POLICY_VERSION


async def record_permission(db, user_id, lead_id, basis, channel="email"):
    basis = (basis or "").strip()
    if len(basis) < 10 or len(basis) > 1000:
        raise ValueError("Describe the existing permission or contact relationship in 10–1000 characters.")
    await db.execute(select(Lead.id).where(Lead.id == lead_id, Lead.user_id == user_id).with_for_update())
    row = await db.scalar(select(ContactPermission).where(
        ContactPermission.user_id == user_id, ContactPermission.lead_id == lead_id,
        ContactPermission.channel == channel))
    if row:
        row.basis = basis
        row.recorded_at = datetime.now(timezone.utc)
        row.revoked_at = None
    else:
        db.add(ContactPermission(user_id=user_id, lead_id=lead_id, channel=channel, basis=basis))
    await db.flush()


async def has_permission(db, user_id, lead_id, channel="email"):
    return bool(await db.scalar(select(ContactPermission.id).where(
        ContactPermission.user_id == user_id, ContactPermission.lead_id == lead_id,
        ContactPermission.channel == channel, ContactPermission.revoked_at.is_(None))))


async def evaluate_outbound(db, user_id, lead: Lead, *, channel: str, message_type: str,
                            subject: str = "", body: str = "", message_id=None) -> ComplianceResult:
    """Call under the operator row lock immediately before a send reservation."""
    settings = get_settings()
    reasons = []
    if not lead or lead.user_id != user_id:
        reasons.append("lead_not_found")
    else:
        if await is_suppressed(db, lead):
            reasons.append("contact_suppressed")
        if not body or not body.strip() or len(body) > 10000 or not subject.strip() and channel == "email":
            reasons.append("invalid_message")
        if channel == "email":
            from pydantic import TypeAdapter, EmailStr, ValidationError
            try:
                TypeAdapter(EmailStr).validate_python(lead.email)
            except (ValidationError, TypeError):
                reasons.append("invalid_recipient")
            if settings.email_provider == "resend" and (not settings.email_enabled or not settings.resend_api_key):
                reasons.append("email_provider_disabled")
            if message_type == "outreach" and settings.email_provider == "resend" and not await has_permission(db, user_id, lead.id):
                reasons.append("permission_not_recorded")
            cap = settings.daily_send_cap
        elif channel == "sms":
            if not normalize_phone(lead.phone):
                reasons.append("invalid_recipient")
            if not settings.sms_enabled or not settings.textbee_api_key:
                reasons.append("sms_provider_disabled")
            cap = settings.daily_sms_cap
        else:
            reasons.append("unsupported_channel")
            cap = 0
        if message_type == "outreach":
            if lead.status != "approved" or not lead.draft_body:
                reasons.append("human_approval_required")
        else:
            message = await db.get(Communication, message_id) if message_id else None
            if not message or message.user_id != user_id or message.lead_id != lead.id or message.direction != "inbound":
                reasons.append("incoming_message_required")
            elif message.status != "approved":
                reasons.append("human_approval_required")
        job = await db.get(Job, lead.job_id) if lead.job_id else None
        campaign = await db.get(Campaign, job.campaign_id) if job and job.campaign_id else None
        if campaign and campaign.paused:
            reasons.append("campaign_paused")
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        sent_today = await db.scalar(select(func.count(SendLog.id)).where(
            SendLog.user_id == user_id, SendLog.channel == channel,
            SendLog.status != "failed", SendLog.sent_at >= start)) or 0
        if sent_today >= cap:
            reasons.append("daily_limit_reached")
        key = (f"reply/{user_id}/{message_id}/v1" if channel == "email" and message_id else
               f"sms-reply/{user_id}/{message_id}/v1" if channel == "sms" else
               f"outreach/{user_id}/{lead.id}/v1")
        if await db.scalar(select(SendLog.id).where(SendLog.idempotency_key == key,
                                                   SendLog.status != "failed")):
            reasons.append("duplicate_message")
    result = ComplianceResult(decision="block" if reasons else "allow",
        reason_codes=reasons, checked_at=datetime.now(timezone.utc))
    if lead and lead.user_id == user_id:
        db.add(ComplianceDecision(user_id=user_id, lead_id=lead.id, channel=channel,
            message_type=message_type, decision=result.decision,
            reason_codes=result.reason_codes, policy_version=POLICY_VERSION,
            checked_at=result.checked_at))
        await db.flush()
    return result
