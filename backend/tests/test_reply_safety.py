from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock

import pytest

from app.services import calendar_service
from app.services.communication_service import stop_reason


@pytest.mark.asyncio
async def test_vague_reply_never_invents_a_meeting(monkeypatch):
    monkeypatch.setattr(calendar_service, "call_llm", AsyncMock(side_effect=RuntimeError("offline")))
    reference = datetime(2026, 10, 12, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    vague = await calendar_service.analyze_reply_and_scheduling(
        "Works for me", "Appointment intake", "Salon", reference)
    assert vague["intent"] == "schedule_inquiry"
    assert vague["start_datetime"] is None
    unrelated = await calendar_service.analyze_reply_and_scheduling(
        "Please send a price list?", "Appointment intake", "Salon", reference)
    assert unrelated["intent"] == "question"
    assert unrelated["start_datetime"] is None


@pytest.mark.asyncio
async def test_explicit_time_is_parsed_without_defaulting_to_three_pm(monkeypatch):
    monkeypatch.setattr(calendar_service, "call_llm", AsyncMock(side_effect=RuntimeError("offline")))
    reference = datetime(2026, 10, 12, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    reply = await calendar_service.analyze_reply_and_scheduling(
        "Tomorrow at 11:30am works", "Appointment intake", "Salon", reference)
    assert reply["intent"] == "meeting_agreed"
    assert reply["start_datetime"].hour == 11
    assert reply["start_datetime"].minute == 30


@pytest.mark.asyncio
async def test_model_cannot_invent_meeting_without_explicit_time(monkeypatch):
    monkeypatch.setattr(calendar_service, "call_llm", AsyncMock(return_value=(
        '{"intent":"meeting_agreed","has_proposed_slot":true,'
        '"proposed_start_iso":"2026-10-13T15:00:00+05:30",'
        '"suggested_reply":"Confirmed for tomorrow"}')))
    reference = datetime(2026, 10, 12, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    reply = await calendar_service.analyze_reply_and_scheduling(
        "Sounds good", "Appointment intake", "Salon", reference)
    assert reply["intent"] == "schedule_inquiry"
    assert reply["start_datetime"] is None
    assert "Confirmed" not in reply["suggested_reply"]


def test_complaint_is_not_treated_as_interest():
    assert stop_reason("This is spam. I will report you.") == "complaint"
