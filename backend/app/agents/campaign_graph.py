"""Campaign planning and discovery are explicit, inspectable graph phases.

Each durable work item invokes one phase. The SQL queue owns retries and polling;
graph invocations never hold a worker slot while the scraper is running.
"""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class CampaignState(TypedDict, total=False):
    campaign_id: str
    job_id: str
    phase: str
    scrape_status: str
    lead_count: int
    outcome: str
    plan_valid: bool


def build_campaign_graph(plan, validate, dispatch, poll, finalize):
    graph = StateGraph(CampaignState)
    graph.add_node("plan", plan)
    graph.add_node("validate_plan", validate)
    graph.add_node("dispatch", dispatch)
    graph.add_node("poll", poll)
    graph.add_node("finalize", finalize)
    graph.add_conditional_edges(START, lambda state: state["phase"],
        {"plan": "plan", "discover": "dispatch"})
    graph.add_edge("plan", "validate_plan")
    graph.add_edge("validate_plan", END)
    graph.add_edge("dispatch", "poll")
    graph.add_conditional_edges("poll", lambda state: "finalize" if state.get("scrape_status") == "completed" else "end",
                                {"finalize": "finalize", "end": END})
    graph.add_edge("finalize", END)
    return graph.compile()
