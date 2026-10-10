"""Two-input campaigns with immutable strategy snapshots and pause/resume control."""
import uuid
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select, func
from app.core.database import get_db
from app.core.security import get_operator_user
from app.models.models import Campaign, Job, Lead, WorkItem, User, CampaignLead, LeadIdea
from app.schemas.schemas import OnboardingRequest

router = APIRouter(tags=["Campaign Orchestrator"])


class CampaignRequest(OnboardingRequest):
    request_id: uuid.UUID = Field(default_factory=uuid.uuid4)


@router.post("/campaigns", status_code=202)
async def start_campaign(payload: CampaignRequest, user=Depends(get_operator_user), db=Depends(get_db)):
    if not payload.service_description.strip() or not payload.target_clients.strip():
        raise HTTPException(422, "Both answers are required.")
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    existing = await db.scalar(select(Campaign).where(Campaign.user_id == user.id, Campaign.request_id == payload.request_id))
    if existing:
        return {"id": str(existing.id), "status": existing.status}
    user.service_description = payload.service_description.strip()
    user.target_clients = payload.target_clients.strip()
    campaign = Campaign(user_id=user.id, request_id=payload.request_id,
        service_description=user.service_description, target_clients=user.target_clients)
    db.add(campaign)
    await db.flush()
    db.add(WorkItem(user_id=user.id, kind="campaign_plan", payload={"campaign_id": str(campaign.id)}))
    await db.commit()
    return {"id": str(campaign.id), "status": campaign.status}


@router.get("/campaigns")
async def campaigns(user=Depends(get_operator_user), db=Depends(get_db)):
    rows = (await db.scalars(select(Campaign).where(Campaign.user_id == user.id).order_by(Campaign.created_at.desc()).limit(50))).all()
    result = []
    for row in rows:
        jobs = (await db.scalars(select(Job).where(Job.campaign_id == row.id))).all()
        counts = dict((await db.execute(select(Lead.outreach_status, func.count(Lead.id))
            .join(CampaignLead, CampaignLead.lead_id == Lead.id).where(CampaignLead.campaign_id == row.id).group_by(Lead.outreach_status))).all())
        status = row.status
        if jobs and all(job.status == "completed" for job in jobs) and not all(job.lead_count == 0 for job in jobs):
            status = "preparing_ideas" if counts.get("queued", 0) + counts.get("processing", 0) else "ready_for_review"
        elif jobs and all(job.status == "completed" and job.lead_count == 0 for job in jobs):
            status = "insufficient_leads"
        if any(job.status == "failed" for job in jobs):
            status = "needs_attention"
        result.append({"id": str(row.id), "service_description": row.service_description,
            "target_clients": row.target_clients, "plan": row.plan, "status": status, "paused": row.paused,
            "counts": counts, "total": sum(counts.values()), "job_ids": [str(job.id) for job in jobs],
            "created_at": row.created_at})
    return result


@router.get("/campaigns/{campaign_id}")
async def campaign_detail(campaign_id: uuid.UUID, user=Depends(get_operator_user), db=Depends(get_db)):
    campaign = await db.scalar(select(Campaign).where(Campaign.id == campaign_id, Campaign.user_id == user.id))
    if not campaign:
        raise HTTPException(404, "Campaign not found.")
    jobs = (await db.scalars(select(Job).where(Job.campaign_id == campaign.id))).all()
    work = (await db.scalars(select(WorkItem).where(WorkItem.user_id == user.id)
            .order_by(WorkItem.created_at).limit(500))).all()
    work = [item for item in work if item.payload and item.payload.get("campaign_id") == str(campaign.id)]
    lead_ids = select(CampaignLead.lead_id).where(CampaignLead.campaign_id == campaign.id)
    counts = dict((await db.execute(select(Lead.outreach_status, func.count(Lead.id))
        .where(Lead.id.in_(lead_ids)).group_by(Lead.outreach_status))).all())
    researched = await db.scalar(select(func.count(Lead.id)).where(
        Lead.id.in_(lead_ids), Lead.research_status == "researched")) or 0
    ideas = await db.scalar(select(func.count(LeadIdea.id)).where(LeadIdea.lead_id.in_(lead_ids))) or 0
    status = campaign.status
    if jobs and all(job.status == "completed" for job in jobs) and not all(job.lead_count == 0 for job in jobs):
        status = "preparing_ideas" if counts.get("queued", 0) + counts.get("processing", 0) else "ready_for_review"
    elif jobs and all(job.status == "completed" and job.lead_count == 0 for job in jobs):
        status = "insufficient_leads"
    if any(job.status == "failed" for job in jobs):
        status = "needs_attention"
    return {"id": str(campaign.id), "status": status, "paused": campaign.paused,
            "service_description": campaign.service_description, "target_clients": campaign.target_clients,
            "counts": counts, "leads_discovered": sum(counts.values()),
            "research_completed": researched, "ideas_generated": ideas,
            "awaiting_approval": counts.get("drafted", 0) + counts.get("approved", 0),
            "plan": campaign.plan, "timeline": [
                {"id": str(item.id), "kind": item.kind, "status": item.status,
                 "at": item.created_at, "completed_at": item.completed_at,
                 "error_message": item.error_message} for item in work],
            "jobs": [{"id": str(job.id), "status": job.status, "lead_count": job.lead_count,
                      "error_message": job.error_message} for job in jobs]}


@router.post("/campaigns/{campaign_id}/{action}")
async def control(campaign_id: uuid.UUID, action: Literal["pause", "resume"],
                  user=Depends(get_operator_user), db=Depends(get_db)):
    row = await db.scalar(select(Campaign).where(Campaign.id == campaign_id, Campaign.user_id == user.id).with_for_update())
    if not row:
        raise HTTPException(404, "Campaign not found.")
    row.paused = action == "pause"
    if not row.paused:
        items = (await db.scalars(select(WorkItem).where(WorkItem.user_id == user.id, WorkItem.status == "pending"))).all()
        for item in items:
            if item.payload.get("campaign_id") == str(row.id):
                item.available_at = datetime.now(timezone.utc)
    await db.commit()
    return {"paused": row.paused}
