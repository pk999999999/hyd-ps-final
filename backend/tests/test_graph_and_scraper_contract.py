import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents import lead_graph
from app.services import scraper_service


@pytest.mark.asyncio
async def test_lead_graph_runs_nodes_once_and_resumes_human_review(monkeypatch):
    calls = []

    async def scout(state):
        calls.append("scout")
        return {"proposed_ideas": [{"hook_name": "Booking", "pitch": "Improve booking"}]}

    async def critic(state):
        calls.append("critic")
        assert state["proposed_ideas"]
        return {"selected_idea": state["proposed_ideas"][0]}

    async def draft(state):
        calls.append("draft")
        assert state["selected_idea"]["hook_name"] == "Booking"
        return {"draft_subject": "A booking idea", "draft_body": "Hello", "awaiting_human_approval": True}

    monkeypatch.setattr(lead_graph, "scout_propose_ideas", scout)
    monkeypatch.setattr(lead_graph, "critic_filter_ideas", critic)
    monkeypatch.setattr(lead_graph, "draft_outreach_email", draft)
    graph = lead_graph.build_lead_graph(InMemorySaver())
    config = lead_graph.lead_graph_config("operator", "business")

    paused = await graph.ainvoke({"lead_id": "business"}, config)
    assert [str(item.value["reason"]) for item in paused["__interrupt__"]] == [
        "An operator must review this outreach draft before delivery."
    ]
    assert (await graph.aget_state(config)).next == ("human_review",)
    assert calls == ["scout", "critic", "draft"]

    resumed = await graph.ainvoke(Command(resume={"action": "edited", "subject": "Updated", "body": "Updated body"}), config)
    assert resumed["human_action"] == "edited"
    assert resumed["final_body"] == "Updated body"
    assert resumed["awaiting_human_approval"] is False
    assert calls == ["scout", "critic", "draft"]


@pytest.mark.asyncio
async def test_city_only_scrape_job_geocodes_before_api_dispatch(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.host == "nominatim.openstreetmap.org":
            return httpx.Response(200, json=[{"lat": "17.385", "lon": "78.4867"}])
        return httpx.Response(201, json={"id": "scraper-123"})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    job = SimpleNamespace(id="job-1", keywords=["dentist"], city="Hyderabad", location_source="manual",
                          latitude=None, longitude=None, depth=5, radius_m=5000, scraper_job_id=None,
                          status="queued", error_message=None)
    session = SimpleNamespace(commit=AsyncMock())
    await scraper_service.ScraperService(session).dispatch(job)

    assert len(requests) == 2
    payload = json.loads(requests[1].content)
    assert payload["lat"] == "17.385"
    assert payload["lon"] == "78.4867"
    assert payload["max_time"] == 600
    assert job.scraper_job_id == "scraper-123"
    assert job.status == "running"
