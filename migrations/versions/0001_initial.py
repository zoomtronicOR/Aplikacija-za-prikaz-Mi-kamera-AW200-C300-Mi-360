"""initial schema: users, sessions, cameras, audit log

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("role IN ('admin', 'user', 'viewer')", name="ck_users_role"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip", sa.String(64)),
    )
    op.create_table(
        "cameras",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("location", sa.String(128)),
        sa.Column("icon", sa.String(32)),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("did", sa.String(32), nullable=False, unique=True),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("mac", sa.String(32)),
        sa.Column("region", sa.String(8), nullable=False, server_default="de"),
        sa.Column("stream_profile", sa.String(16)),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("username", sa.String(64)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(128)),
        sa.Column("detail", sa.Text),
        sa.Column("ip", sa.String(64)),
    )


def downgrade() -> None:
    for t in ("audit_log", "cameras", "sessions", "users"):
        op.drop_table(t)
