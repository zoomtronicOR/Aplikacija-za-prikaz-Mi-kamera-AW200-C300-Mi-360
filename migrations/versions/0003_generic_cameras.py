"""generic (non-Xiaomi) cameras: kind column, Xiaomi-only columns become nullable

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cameras", sa.Column("kind", sa.String(16), nullable=False, server_default="xiaomi"))
    # stream source with the password masked; the real URL lives only in go2rtc.yaml
    op.add_column("cameras", sa.Column("source_display", sa.String(512)))
    op.alter_column("cameras", "model", nullable=True)
    op.alter_column("cameras", "did", nullable=True)
    op.alter_column("cameras", "ip", nullable=True)
    op.create_check_constraint("ck_cameras_kind", "cameras", "kind IN ('xiaomi', 'generic')")


def downgrade() -> None:
    op.drop_constraint("ck_cameras_kind", "cameras")
    op.execute("DELETE FROM cameras WHERE kind <> 'xiaomi'")
    op.alter_column("cameras", "ip", nullable=False)
    op.alter_column("cameras", "did", nullable=False)
    op.alter_column("cameras", "model", nullable=False)
    op.drop_column("cameras", "source_display")
    op.drop_column("cameras", "kind")
