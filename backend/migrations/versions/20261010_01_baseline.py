"""Versioned baseline for fresh installations and pre-Alembic databases.

Revision ID: 20261010_01
Revises:
"""
from alembic import op
import hashlib
import uuid
from sqlalchemy import Column, Integer, String, Text, JSON, Uuid, inspect, table, column, update

revision = "20261010_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    from app.core.database import Base, _upgrade_existing_tables
    import app.models.models  # noqa: F401

    bind = op.get_bind()
    # Existing installations predate Alembic; create_all only fills missing
    # tables. Legacy column changes are then applied and this revision stamped.
    Base.metadata.create_all(bind)
    _upgrade_existing_tables(bind)
    columns = {item["name"] for item in inspect(bind).get_columns("leads")}
    if "lead_score" not in columns:
        op.add_column("leads", Column("lead_score", Integer(),
                      nullable=False, server_default="0"))
    if "lead_score_factors" not in columns:
        op.add_column("leads", Column("lead_score_factors", JSON(), nullable=False, server_default="{}"))
    if "research_status" not in columns:
        op.add_column("leads", Column("research_status", String(30),
                      nullable=False, server_default="pending"))
    evidence_columns = {item["name"] for item in inspect(bind).get_columns("lead_evidence")}
    if "source_key" not in evidence_columns:
        op.add_column("lead_evidence", Column("source_key", String(64), nullable=False, server_default=""))
        rows = bind.exec_driver_sql("SELECT id, lead_id, source_kind, source_url, title FROM lead_evidence ORDER BY id").all()
        evidence_table = table("lead_evidence", column("id", Uuid(as_uuid=True)), column("source_key", String(64)))
        used = set()
        for row in rows:
            key = hashlib.sha256(f"{row.source_kind}\0{row.source_url}\0{row.title}".encode()).hexdigest()
            # Legacy rows may repeat the same source; preserve both observations.
            identity = (str(row.lead_id), key)
            if identity in used:
                key = hashlib.sha256(f"{key}\0{row.id}".encode()).hexdigest()
                identity = (str(row.lead_id), key)
            used.add(identity)
            bind.execute(update(evidence_table).where(evidence_table.c.id == uuid.UUID(str(row.id))).values(source_key=key))
        with op.batch_alter_table("lead_evidence") as batch:
            batch.create_unique_constraint("uq_lead_evidence_source_key", ["lead_id", "source_key"])
    idea_columns = {item["name"] for item in inspect(bind).get_columns("lead_ideas")}
    for name in ("potential_benefit", "uncertainty", "implementation", "suggested_cta"):
        if name not in idea_columns:
            op.add_column("lead_ideas", Column(name, Text(), nullable=False, server_default=""))
    if "confidence" not in idea_columns:
        op.add_column("lead_ideas", Column("confidence", String(30), nullable=False, server_default="hypothesis"))


def downgrade():
    # Baseline also adopts pre-existing user data; irreversible drop is unsafe.
    raise RuntimeError("The baseline migration cannot safely remove an existing database.")
