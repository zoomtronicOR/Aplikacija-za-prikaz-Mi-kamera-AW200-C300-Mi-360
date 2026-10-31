"""system health: share capacity, disk alarm settings and state

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # written by the recorder when it checks the share
    op.add_column("recording_storage", sa.Column("share_total_bytes", sa.BigInteger))
    op.add_column("recording_storage", sa.Column("share_free_bytes", sa.BigInteger))
    op.add_column("notify_settings", sa.Column("on_disk", sa.Boolean, nullable=False, server_default=sa.true()))
    op.add_column("notify_settings", sa.Column("disk_warn_pct", sa.Integer, nullable=False, server_default="80"))
    op.add_column("notify_settings", sa.Column("disk_crit_pct", sa.Integer, nullable=False, server_default="90"))
    # last alarm level per disk (0 ok, 1 warning, 2 critical), so an alarm fires once per crossing
    op.create_table(
        "system_alerts",
        sa.Column("target", sa.String(32), primary_key=True),
        sa.Column("level", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("system_alerts")
    for c in ("disk_crit_pct", "disk_warn_pct", "on_disk"):
        op.drop_column("notify_settings", c)
    op.drop_column("recording_storage", "share_free_bytes")
    op.drop_column("recording_storage", "share_total_bytes")
