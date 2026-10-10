import pytest
from app.agents.campaign_graph import build_campaign_graph


@pytest.mark.asyncio
async def test_campaign_graph_runs_plan_validation_and_completed_discovery():
    calls = []
    async def plan(state):
        calls.append("plan")
        return {"phase": "planned"}
    async def validate(state):
        calls.append("validate")
        return {"plan_valid": True}
    async def dispatch(state):
        calls.append("dispatch")
        return {"job_id": "job-1"}
    async def poll(state):
        calls.append("poll")
        return {"scrape_status": "completed", "lead_count": 2}
    async def finalize(state):
        calls.append("finalize")
        return {"outcome": "leads_imported"}
    graph = build_campaign_graph(plan, validate, dispatch, poll, finalize)
    assert (await graph.ainvoke({"campaign_id": "campaign-1", "phase": "plan"}))["plan_valid"]
    assert calls == ["plan", "validate"]
    calls.clear()
    assert (await graph.ainvoke({"campaign_id": "campaign-1", "phase": "discover"}))["outcome"] == "leads_imported"
    assert calls == ["dispatch", "poll", "finalize"]


@pytest.mark.asyncio
async def test_campaign_graph_does_not_finalize_pending_scrape():
    calls = []
    async def noop(state):
        calls.append("noop")
        return {}
    async def poll(state):
        calls.append("poll")
        return {"scrape_status": "running"}
    graph = build_campaign_graph(noop, noop, noop, poll, noop)
    result = await graph.ainvoke({"phase": "discover"})
    assert result["scrape_status"] == "running"
    assert calls == ["noop", "poll"]
