"""Operator-scoped analytics and safe configuration overview."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from app.core.database import get_db
from app.core.security import get_operator_user
from app.core.config import get_settings
from app.models.models import Lead, Campaign, Communication, LeadEvidence, LeadIdea, SendLog, Job, ScheduledEvent

router = APIRouter(tags=["Insights"])


@router.get("/ideas")
async def ideas(user=Depends(get_operator_user), db=Depends(get_db),
                limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    rows = (await db.execute(select(LeadIdea, Lead, LeadEvidence)
        .join(Lead, LeadIdea.lead_id == Lead.id)
        .outerjoin(LeadEvidence, LeadIdea.evidence_id == LeadEvidence.id)
        .where(Lead.user_id == user.id)
        .order_by(LeadIdea.created_at.desc()).offset(offset).limit(limit))).all()
    return [{"id": str(idea.id), "lead_id": str(lead.id), "business_name": lead.title,
             "lead_score": lead.lead_score, "title": idea.hook_name, "problem": idea.problem,
             "proposal": idea.pitch, "status": idea.status,
             "potential_benefit": idea.potential_benefit, "uncertainty": idea.uncertainty,
             "implementation": idea.implementation, "suggested_cta": idea.suggested_cta,
             "evidence": evidence.excerpt if evidence else "No direct evidence reference; hypothesis for review.",
             "source_url": evidence.source_url if evidence else "",
             "confidence": idea.confidence,
             "draft_subject": lead.draft_subject, "draft_body": lead.draft_body} for idea, lead, evidence in rows]


@router.get("/analytics")
async def analytics(user=Depends(get_operator_user), db=Depends(get_db)):
    lead_rows = (await db.execute(select(Lead.outreach_status, func.count(Lead.id))
                 .where(Lead.user_id == user.id).group_by(Lead.outreach_status))).all()
    inbox_rows = (await db.execute(select(Communication.classification, func.count(Communication.id))
                  .where(Communication.user_id == user.id).group_by(Communication.classification))).all()
    delivery_rows = (await db.execute(select(SendLog.status, func.count(SendLog.id))
        .where(SendLog.user_id == user.id).group_by(SendLog.status))).all()
    job_rows = (await db.execute(select(Job.status, func.count(Job.id))
        .where(Job.user_id == user.id).group_by(Job.status))).all()
    idea_rows = (await db.execute(select(LeadIdea.hook_name, func.count(LeadIdea.id)).join(Lead)
        .where(Lead.user_id == user.id).group_by(LeadIdea.hook_name)
        .order_by(func.count(LeadIdea.id).desc()).limit(10))).all()
    contacted = await db.scalar(select(func.count(func.distinct(SendLog.lead_id))).where(
        SendLog.user_id == user.id, SendLog.status.in_(("sent", "delivered")))) or 0
    contacted_leads = select(SendLog.lead_id).where(
        SendLog.user_id == user.id, SendLog.status.in_(("sent", "delivered")))
    replied = await db.scalar(select(func.count(func.distinct(Communication.lead_id))).where(
        Communication.user_id == user.id, Communication.direction == "inbound",
        Communication.lead_id.in_(contacted_leads))) or 0
    positive = await db.scalar(select(func.count(func.distinct(Communication.lead_id))).where(
        Communication.user_id == user.id, Communication.direction == "inbound",
        Communication.lead_id.in_(contacted_leads),
        Communication.classification.in_(("interested", "callback", "schedule_inquiry", "meeting_scheduled")))) or 0
    opted_out = await db.scalar(select(func.count(Lead.id)).where(Lead.user_id == user.id, Lead.opted_out.is_(True))) or 0
    meetings = await db.scalar(select(func.count(ScheduledEvent.id)).where(
        ScheduledEvent.user_id == user.id, ScheduledEvent.status == "confirmed")) or 0
    total_leads = sum(count for _, count in lead_rows)
    researched = await db.scalar(select(func.count(Lead.id)).where(
        Lead.user_id == user.id, Lead.research_status == "researched")) or 0
    def rate(numerator, denominator):
        return {"numerator": numerator, "denominator": denominator,
                "percent": round(numerator * 100 / denominator, 1) if denominator else 0}
    return {"leads_by_status": dict(lead_rows), "replies_by_intent": dict(inbox_rows),
            "discovery_jobs_by_status": dict(job_rows), "idea_categories": dict(idea_rows),
            "delivery_outcomes": dict(delivery_rows), "research_completed": researched,
            "meetings_booked": meetings,
            "rates": {"research_coverage": rate(researched, total_leads),
                      "reply_rate": rate(replied, contacted),
                      "positive_response_rate": rate(positive, replied),
                      "opt_out_rate": rate(opted_out, contacted),
                      "meeting_rate": rate(meetings, contacted)},
            "campaigns": await db.scalar(select(func.count(Campaign.id)).where(Campaign.user_id == user.id)) or 0,
            "evidence": await db.scalar(select(func.count(LeadEvidence.id)).join(Lead).where(Lead.user_id == user.id)) or 0,
            "messages_sent": await db.scalar(select(func.count(SendLog.id)).where(SendLog.user_id == user.id,
                SendLog.status.in_(("sent", "delivered")))) or 0,
            "average_lead_score": round(float(await db.scalar(select(func.avg(Lead.lead_score))
                .where(Lead.user_id == user.id)) or 0), 1)}


@router.get("/settings/overview")
async def settings_overview(user=Depends(get_operator_user)):
    settings = get_settings()
    from app.services.model_provider import ProviderError, validate_provider_configuration
    try:
        provider, model = validate_provider_configuration(settings)
        llm_configured = True
    except ProviderError:
        provider, model, llm_configured = settings.llm_provider, "unconfigured", False
    return {"service_description": user.service_description, "target_clients": user.target_clients,
            "research_provider": "tavily" if settings.tavily_api_key else "website_and_maps",
            "tavily_configured": bool(settings.tavily_api_key),
            "scraper_configured": bool(settings.scraper_base_url),
            "llm_provider": provider, "llm_model": model,
            "approval_required": True,
            "demo_mode": settings.demo_mode, "email_provider": settings.email_provider,
            "email_enabled": settings.email_enabled, "daily_email_cap": settings.daily_email_cap,
            "sms_enabled": settings.sms_enabled, "daily_sms_cap": settings.daily_sms_cap,
            "llm_configured": llm_configured}
