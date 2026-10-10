"""Durable pipeline orchestrator coordinating Scout, Critic, Comms and human checkpoints."""

import logging
import uuid
import asyncio
from html import escape
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import Lead, User, Job, Campaign, LeadIdea, LeadEvidence
from app.agents.state import LeadWorkflowState
from app.agents.lead_graph import build_lead_graph, lead_checkpointer, lead_graph_config
from langgraph.types import Command
from app.services.memory_service import MemoryService
from app.services.email_service import EmailService

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, db_session: AsyncSession, memory_service: MemoryService):
        self.session = db_session
        self.memory = memory_service

    async def run_pipeline_to_checkpoint(
        self,
        operator: User,
        lead: Lead,
    ) -> LeadWorkflowState:
        """Executes Scout -> Critic -> Comms Draft and pauses at human checkpoint."""
        # 1. Fetch learned preferences & past few-shot decisions from Mongo
        style_rules, past_decisions, outcomes = await asyncio.gather(
            self.memory.get_operator_rules(str(operator.id)),
            self.memory.get_recent_decisions(str(operator.id), checkpoint_type="message_draft", limit=3),
            self.memory.get_recent_outcomes(str(operator.id), 3))

        # The campaign strategy stays fixed even when the operator launches a new search.
        job = await self.session.get(Job, lead.job_id) if lead.job_id else None
        campaign = await self.session.get(Campaign, job.campaign_id) if job and job.campaign_id else None
        service = campaign.service_description if campaign else operator.service_offered
        targets = campaign.target_clients if campaign else operator.target_clients
        # 2. Build initial state
        state: LeadWorkflowState = {
            "operator_id": str(operator.id),
            "lead_id": str(lead.id),
            "service_offered": service or "AI receptionist & appointment scheduling",
            "target_clients": targets or "Local businesses in Hyderabad",
            "business_title": lead.title,
            "category": lead.category or "Local Business",
            "website": lead.website,
            "email": lead.email,
            "phone": lead.phone,
            "rating": lead.review_rating,
            "review_count": lead.review_count,
            "address": lead.address,
            "raw_data": lead.raw_data,
            "evidence": [],
            "lead_score": lead.lead_score or 0,
            "style_rules": style_rules,
            "few_shot_examples": past_decisions + outcomes,
            "proposed_ideas": [],
            "critique_notes": "",
            "selected_idea": None,
            "draft_subject": None,
            "draft_body": None,
            "awaiting_human_approval": False,
            "human_action": None,
            "final_subject": None,
            "final_body": None,
            "feedback_tag": None,
            "current_step": "init",
            "send_success": False,
            "status_message": "Pipeline initiated",
        }

        async def research_node(_state):
            from app.services.research_service import research_lead
            evidence = await research_lead(self.session, lead)
            return {"evidence": evidence, "lead_score": lead.lead_score,
                    "current_step": "researched"}

        async with lead_checkpointer(self.session) as checkpointer:
            graph = build_lead_graph(checkpointer, research_node)
            state = await graph.ainvoke(state, lead_graph_config(str(operator.id), str(lead.id)))
        if not state.get("__interrupt__") or not state.get("draft_body"):
            raise RuntimeError("The lead graph did not reach its human review checkpoint.")

        # 3. Persist drafted state to PostgreSQL lead
        from app.services.communication_service import is_suppressed
        await self.session.refresh(lead)
        if await is_suppressed(self.session, lead) or lead.status in ("replied", "contacted"):
            raise ValueError("Contact suppression or a reply stopped this draft.")
        lead.pitch_hook = (state.get("selected_idea") or {}).get("hook_name", "")
        lead.proposed_idea = (state.get("selected_idea") or {}).get("pitch", "")
        lead.business_summary = f"{lead.title} is a {lead.category or 'local business'} at {lead.address or 'an unspecified location'}."
        lead.draft_subject = state.get("draft_subject")
        lead.draft_body = state.get("draft_body")
        lead.status = "drafted"
        from sqlalchemy import delete
        await self.session.execute(delete(LeadIdea).where(LeadIdea.lead_id == lead.id))
        evidence_ids = {item["id"] for item in state.get("evidence") or []}
        selected = state.get("selected_idea") or {}
        for idea in state.get("proposed_ideas") or []:
            evidence_id = idea.get("evidence_id")
            if evidence_id not in evidence_ids:
                evidence_id = None
            self.session.add(LeadIdea(lead_id=lead.id, evidence_id=uuid.UUID(evidence_id) if evidence_id else None,
                hook_name=str(idea.get("hook_name") or "Idea")[:500], problem=str(idea.get("problem") or ""),
                pitch=str(idea.get("pitch") or ""),
                potential_benefit=str(idea.get("potential_benefit") or ""),
                uncertainty=str(idea.get("uncertainty") or "Unverified business need; discuss with the operator."),
                implementation=str(idea.get("implementation") or ""),
                suggested_cta=str(idea.get("suggested_cta") or ""),
                confidence="source_supported" if evidence_id else "hypothesis",
                status="selected" if idea == selected else "proposed"))
        await self.session.commit()

        return state

    async def execute_human_decision(
        self,
        operator: User,
        lead: Lead,
        action: str,  # "approved", "edited", "rejected"
        final_subject: Optional[str] = None,
        final_body: Optional[str] = None,
        feedback_tag: Optional[str] = None,
        notes: Optional[str] = None,
        send_email: bool = False,
    ) -> Dict[str, Any]:
        """Resumes pipeline from human checkpoint, records learning in Mongo, and dispatches if approved."""
        subject = final_subject or lead.draft_subject or f"Quick question regarding {lead.title}"
        body = final_body or lead.draft_body or ""

        # Resume the saved review node. Legacy drafts without a graph checkpoint
        # remain reviewable, since their SQL record is still authoritative.
        async with lead_checkpointer(self.session) as checkpointer:
            graph = build_lead_graph(checkpointer)
            config = lead_graph_config(str(operator.id), str(lead.id))
            snapshot = await graph.aget_state(config)
            if snapshot.next:
                resumed = await graph.ainvoke(Command(resume={"action": action,
                    "subject": subject, "body": body}), config)
                if resumed.get("human_action") != action:
                    raise RuntimeError("The lead graph did not accept the review decision.")

        # 1. Record decision in MongoDB for in-context learning
        proposal = {
            "pitch_hook": lead.pitch_hook,
            "draft_subject": lead.draft_subject,
            "draft_body": lead.draft_body,
        }
        final_content = {"subject": subject, "body": body}

        decision_id = await self.memory.record_decision(
            operator_id=str(operator.id),
            lead_id=str(lead.id),
            checkpoint_type="message_draft",
            agent_proposal=proposal,
            human_action=action,
            final_content=final_content,
            feedback_tag=feedback_tag,
            notes=notes,
        )

        result_message = ""
        send_ok = False

        if action == "rejected":
            lead.status = "rejected"
            await self.session.commit()
            result_message = "Draft rejected by operator. Lead marked as rejected."
        else:
            # Action is approved or edited
            lead.status = "approved"
            lead.draft_subject = subject
            lead.draft_body = body
            await self.session.commit()

            if send_email:
                email_svc = EmailService(self.session)
                send_ok, msg = await email_svc.send_outreach_email(
                user_id=operator.id,
                lead=lead,
                subject=subject,
                body_html=f"<div>{escape(body).replace(chr(10), '<br>')}</div>",
                body_text=body,
                )
                result_message = msg
            else:
                result_message = "Draft approved and saved. No email has been sent."

        return {
            "decision_id": decision_id,
            "action": action,
            "sent": send_ok,
            "message": result_message,
        }
