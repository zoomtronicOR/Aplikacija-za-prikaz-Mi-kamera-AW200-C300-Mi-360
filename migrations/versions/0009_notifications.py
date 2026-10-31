"""notifications: events table, channels, rules, per-camera motion switch

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
        sa.Column("camera_id", sa.Integer, sa.ForeignKey("cameras.id", ondelete="SET NULL"), index=True),
        sa.Column("kind", sa.String(32), nullable=False),  # motion_start | motion_end | camera_offline | ...
        sa.Column("detail", sa.Text),
        sa.Column("processed", sa.Boolean, nullable=False, server_default=sa.false(), index=True),
    )
    op.create_table(
        "notify_channels",
        sa.Column("kind", sa.String(16), primary_key=True),  # telegram | mqtt | ntfy | email
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("config", sa.Text, nullable=False, server_default="{}"),  # JSON, no secrets
        sa.Column("secret_enc", sa.Text),  # Fernet: bot token / password / access token
        sa.Column("last_ok_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
    )
    op.create_table(
        "notify_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("on_motion", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("on_camera_offline", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("on_camera_online", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("on_storage", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("with_snapshot", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("cooldown_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("cooldown_minutes", sa.Integer, nullable=False, server_default="5"),
        sa.Column("quiet_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("quiet_start", sa.Time),
        sa.Column("quiet_end", sa.Time),
    )
    op.add_column("cameras", sa.Column("notify_motion", sa.Boolean, nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("cameras", "notify_motion")
    for t in ("notify_settings", "notify_channels", "events"):
        op.drop_table(t)
