"""motion detection status per camera

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recording_settings", sa.Column("last_motion_at", sa.DateTime(timezone=True)))
    # share of the picture that changed (0..1) in the last analysed frame, for tuning sensitivity
    op.add_column("recording_settings", sa.Column("motion_level", sa.Float))


def downgrade() -> None:
    op.drop_column("recording_settings", "motion_level")
    op.drop_column("recording_settings", "last_motion_at")
