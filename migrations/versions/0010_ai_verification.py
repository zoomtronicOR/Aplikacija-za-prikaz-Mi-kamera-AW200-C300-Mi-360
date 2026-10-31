"""AI verification of motion (person / vehicle / animal), each part switchable

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, default in (("ai_enabled", sa.false()), ("ai_person", sa.true()),
                          ("ai_vehicle", sa.false()), ("ai_animal", sa.false())):
        op.add_column("recording_settings", sa.Column(name, sa.Boolean, nullable=False, server_default=default))
    op.add_column("recording_settings", sa.Column("ai_min_score", sa.Float, nullable=False, server_default="0.5"))
    # written by the recorder: what the AI saw last ("osoba 87%") and when
    op.add_column("recording_settings", sa.Column("ai_last", sa.String(64)))
    op.add_column("recording_settings", sa.Column("ai_last_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    for c in ("ai_last_at", "ai_last", "ai_min_score", "ai_animal", "ai_vehicle", "ai_person", "ai_enabled"):
        op.drop_column("recording_settings", c)
