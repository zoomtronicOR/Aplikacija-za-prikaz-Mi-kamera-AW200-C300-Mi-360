"""cameras.sd_stream: optional go2rtc stream name of the camera's SD profile

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cameras", sa.Column("sd_stream", sa.String(64)))


def downgrade() -> None:
    op.drop_column("cameras", "sd_stream")
