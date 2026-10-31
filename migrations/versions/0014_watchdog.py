"""watchdog switches: detection, automatic repair, notifications

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

COLUMNS = ("watchdog_enabled", "watchdog_repair", "on_video")


def upgrade() -> None:
    for c in COLUMNS:
        op.add_column("notify_settings", sa.Column(c, sa.Boolean, nullable=False, server_default=sa.true()))


def downgrade() -> None:
    for c in COLUMNS:
        op.drop_column("notify_settings", c)
