"""event archive: thumbnail per event, retention and thumbnail switch

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("thumb", sa.LargeBinary))  # JPEG from the SD stream
    op.create_index("ix_events_camera_ts", "events", ["camera_id", "ts"])
    op.add_column("notify_settings", sa.Column("event_thumbs", sa.Boolean, nullable=False, server_default=sa.true()))
    op.add_column("notify_settings", sa.Column("events_keep_days", sa.Integer, nullable=False, server_default="30"))


def downgrade() -> None:
    op.drop_column("notify_settings", "events_keep_days")
    op.drop_column("notify_settings", "event_thumbs")
    op.drop_index("ix_events_camera_ts", "events")
    op.drop_column("events", "thumb")
