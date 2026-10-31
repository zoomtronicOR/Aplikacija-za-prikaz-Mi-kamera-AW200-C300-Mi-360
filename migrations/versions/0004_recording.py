"""recording: storage target, per-camera settings, recordings index

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recording_storage",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(8), nullable=False, server_default="smb"),
        sa.Column("host", sa.String(255)),
        sa.Column("share", sa.String(255)),
        sa.Column("folder", sa.String(255)),
        sa.Column("username", sa.String(128)),
        sa.Column("password_enc", sa.Text),
        sa.Column("default_retention_days", sa.Integer, nullable=False, server_default="14"),
        sa.Column("buffer_max_gb", sa.Float, nullable=False, server_default="5"),
        sa.Column("status_ok", sa.Boolean),
        sa.Column("status_error", sa.Text),
        sa.Column("status_at", sa.DateTime(timezone=True)),
        sa.Column("pending_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.CheckConstraint("kind IN ('smb')", name="ck_recording_storage_kind"),
    )
    op.create_table(
        "recording_settings",
        sa.Column("camera_id", sa.Integer, sa.ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("mode", sa.String(16), nullable=False, server_default="off"),
        sa.Column("schedule_start", sa.Time),
        sa.Column("schedule_end", sa.Time),
        sa.Column("schedule_days", sa.String(7), nullable=False, server_default="0123456"),
        sa.Column("retention_days", sa.Integer),
        sa.Column("manual_active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("manual_by", sa.String(64)),
        sa.Column("motion_sensitivity", sa.Integer, nullable=False, server_default="50"),
        sa.Column("rec_active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("rec_reason", sa.String(16)),
        sa.Column("status_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.CheckConstraint("mode IN ('off', 'continuous', 'schedule', 'motion')", name="ck_recording_settings_mode"),
    )
    op.create_table(
        "recordings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("camera_id", sa.Integer, sa.ForeignKey("cameras.id", ondelete="SET NULL"), index=True),
        sa.Column("camera_slug", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    for t in ("recordings", "recording_settings", "recording_storage"):
        op.drop_table(t)
