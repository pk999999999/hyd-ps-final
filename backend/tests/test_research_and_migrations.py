from pathlib import Path
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.database import Base
from app.services import research_service


def test_alembic_baseline_adopts_fresh_and_legacy_database(tmp_path):
    config_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    for legacy in (False, True):
        engine = create_engine(f"sqlite:///{tmp_path / ('legacy.db' if legacy else 'fresh.db')}")
        with engine.begin() as connection:
            if legacy:
                Base.metadata.create_all(connection)
                connection.execute(text("ALTER TABLE leads DROP COLUMN lead_score"))
                connection.execute(text("ALTER TABLE leads DROP COLUMN lead_score_factors"))
                connection.execute(text("ALTER TABLE leads DROP COLUMN research_status"))
                connection.execute(text("DROP TABLE lead_ideas"))
                connection.execute(text("DROP TABLE lead_evidence"))
                connection.execute(text("CREATE TABLE lead_evidence (id CHAR(32) PRIMARY KEY, lead_id CHAR(32) NOT NULL, source_kind VARCHAR(30) NOT NULL, source_url VARCHAR(1000) NOT NULL, title VARCHAR(500) NOT NULL, excerpt TEXT NOT NULL, confidence VARCHAR(20) NOT NULL, observed_at DATETIME NOT NULL)"))
                lead_id = uuid.uuid4().hex
                for _ in range(2):
                    connection.execute(text("INSERT INTO lead_evidence (id, lead_id, source_kind, source_url, title, excerpt, confidence, observed_at) VALUES (:id, :lead_id, 'maps', 'https://maps.example/1', 'Listing', 'Original observation', 'observed', CURRENT_TIMESTAMP)"),
                        {"id": uuid.uuid4().hex, "lead_id": lead_id})
            config = Config(str(config_path))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            assert {"lead_score", "lead_score_factors", "research_status"} <= {column["name"] for column in inspect(connection).get_columns("leads")}
            assert {"lead_evidence", "lead_ideas", "research_runs", "contact_permissions", "compliance_decisions"} <= set(inspect(connection).get_table_names())
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261010_01"
            if legacy:
                rows = connection.execute(text("SELECT source_key FROM lead_evidence")).all()
                assert len(rows) == 2 and all(row.source_key for row in rows)
                assert rows[0].source_key != rows[1].source_key
        engine.dispose()


@pytest.mark.asyncio
async def test_research_records_source_provenance_and_idempotent_score(harness, monkeypatch):
    client, factory, _ = harness
    from conftest import signup
    from app.models.models import Lead, User
    from sqlalchemy import select
    _, headers = await signup(client)
    async with factory() as db:
        user = await db.scalar(select(User))
        lead = Lead(user_id=user.id, title="Sample Bakery", category="Bakery", address="Hyderabad",
                    emails="owner@example.com", website="https://sample.example", google_maps_url="https://maps.example/1")
        db.add(lead)
        await db.commit()
        await db.refresh(lead)
        monkeypatch.setattr(research_service, "website_excerpt", lambda _url: __import__("asyncio").sleep(0, result="Bread and pastries sold daily."))
        monkeypatch.setattr(research_service, "tavily_results", lambda _lead: __import__("asyncio").sleep(0, result=[
            {"url": "https://directory.example/bakery", "title": "Sample Bakery listing", "content": "Bakery in Hyderabad"}]))
        first = await research_service.research_lead(db, lead)
        second = await research_service.research_lead(db, lead)
        assert len(first) == len(second) == 3
        assert lead.lead_score == 75
        await db.commit()
    response = await client.get(f"/api/v1/leads/{lead.id}/research", headers=headers)
    assert response.status_code == 200
    assert {item["source_kind"] for item in response.json()["evidence"]} == {"maps", "website", "search"}
    queued = await client.post(f"/api/v1/leads/{lead.id}/research", headers=headers)
    assert queued.status_code == 202
    from app import worker
    for item_id in await worker.claim_work(10):
        await worker.process_work(item_id)
    assert (await client.get(f"/api/v1/leads/{lead.id}/research", headers=headers)).json()["research_status"] == "researched"


def test_private_sites_and_unrelated_results_are_rejected():
    assert research_service.public_url("http://127.0.0.1/admin") is False
    assert research_service.public_url("http://localhost/admin") is False
    assert research_service.public_url("file:///etc/passwd") is False
    lead = type("Business", (), {"title": "Sample Bakery"})()
    assert research_service.matches_business(lead, {"title": "Other Bakery", "content": "Hyderabad"}) is False
