"""cameras: spec-derived controls for unknown Xiaomi models, per-camera PTZ switch

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cameras", sa.Column("controls_json", sa.Text))
    op.add_column("cameras", sa.Column("ptz", sa.Boolean))


def downgrade() -> None:
    op.drop_column("cameras", "ptz")
    op.drop_column("cameras", "controls_json")
