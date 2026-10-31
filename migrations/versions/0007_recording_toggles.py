"""on/off switches for every optional recording feature (user requirement)

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

COLUMNS = ("motion_zones_enabled", "motion_noise_adapt", "motion_require_group", "motion_pre_roll", "record_audio")


def upgrade() -> None:
    for c in COLUMNS:
        op.add_column("recording_settings", sa.Column(c, sa.Boolean, nullable=False, server_default=sa.true()))


def downgrade() -> None:
    for c in COLUMNS:
        op.drop_column("recording_settings", c)
