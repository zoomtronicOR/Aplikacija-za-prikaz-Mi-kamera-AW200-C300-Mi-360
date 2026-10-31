"""motion zones: user ignore mask, automatic suppression of restless areas, live block state

Masks are 144-character strings of '0'/'1', one per 10x10 px block of the 160x90 analysis frame
(9 rows x 16 columns, row by row).

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recording_settings", sa.Column("motion_mask", sa.String(144)))  # user: 1 = ignore
    op.add_column("recording_settings", sa.Column("motion_auto_ignore", sa.Boolean, nullable=False,
                                                  server_default=sa.true()))
    # written by the recorder: blocks moving right now, and blocks suppressed as restless
    op.add_column("recording_settings", sa.Column("motion_live", sa.String(144)))
    op.add_column("recording_settings", sa.Column("motion_suppressed", sa.String(144)))


def downgrade() -> None:
    for c in ("motion_suppressed", "motion_live", "motion_auto_ignore", "motion_mask"):
        op.drop_column("recording_settings", c)
