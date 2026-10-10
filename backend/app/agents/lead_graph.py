"""LangGraph lead preparation with a durable human-review interruption.

The SQL lead and work queue remain the business source of truth. The checkpoint
records which graph node ran, so an approval can resume without regenerating a
potentially different proposal or sending anything as a side effect.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.comms_agent import draft_outreach_email
from app.agents.scout_agent import critic_filter_ideas, scout_propose_ideas
from app.agents.state import LeadWorkflowState

_test_checkpointer = InMemorySaver()


async def human_review(state: LeadWorkflowState) -> dict:
    decision = interrupt({
        "lead_id": state["lead_id"],
        "subject": state["draft_subject"],
        "body": state["draft_body"],
        "reason": "An operator must review this outreach draft before delivery.",
    })
    if not isinstance(decision, dict) or decision.get("action") not in {"approved", "edited", "rejected"}:
        raise ValueError("A valid human review decision is required.")
    return {
        "human_action": decision["action"],
        "final_subject": decision.get("subject") or state["draft_subject"],
        "final_body": decision.get("body") or state["draft_body"],
        "awaiting_human_approval": False,
        "current_step": "reviewed",
    }


def build_lead_graph(checkpointer, research_node=None):
    graph = StateGraph(LeadWorkflowState)
    async def no_research(state):
        return {"evidence": state.get("evidence") or [], "lead_score": state.get("lead_score") or 0}
    graph.add_node("research", research_node or no_research)
    graph.add_node("scout", scout_propose_ideas)
    graph.add_node("critic", critic_filter_ideas)
    graph.add_node("draft", draft_outreach_email)
    graph.add_node("human_review", human_review)
    graph.add_edge(START, "research")
    graph.add_edge("research", "scout")
    graph.add_edge("scout", "critic")
    graph.add_edge("critic", "draft")
    graph.add_edge("draft", "human_review")
    graph.add_edge("human_review", END)
    return graph.compile(checkpointer=checkpointer)


@asynccontextmanager
async def lead_checkpointer(session) -> AsyncIterator:
    """Use PostgreSQL checkpoints in deployment and an isolated saver in tests."""
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        yield _test_checkpointer
        return
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    url = bind.url.set(drivername="postgresql").render_as_string(hide_password=False)
    async with AsyncPostgresSaver.from_conn_string(url) as checkpointer:
        await checkpointer.setup()
        yield checkpointer


def lead_graph_config(operator_id: str, lead_id: str) -> dict:
    return {"configurable": {"thread_id": f"lead:{operator_id}:{lead_id}"}}
