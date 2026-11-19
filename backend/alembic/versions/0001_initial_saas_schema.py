"""Initial SmartResearch FMSB SaaS database schema.

Revision ID: 0001_initial_saas_schema
Revises:
Create Date: 2026-11-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_saas_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_VARIANT = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("matricule", sa.String(length=64), nullable=False),
        sa.Column("phone", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("active_session_id", sa.String(length=64), nullable=True),
        sa.Column("session_switch_count", sa.Integer(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_admin_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_role", "users", ["role"], unique=False)

    op.create_table(
        "license_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price_fcfa", sa.Integer(), nullable=False),
        sa.Column("module1_days", sa.Integer(), nullable=False),
        sa.Column("module2_days", sa.Integer(), nullable=False),
        sa.Column("module3_days", sa.Integer(), nullable=False),
        sa.Column("module4_days", sa.Integer(), nullable=False),
        sa.Column("ai_quota_included", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "user_license_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=True),
        sa.Column("granted_by_admin_id", sa.String(length=36), nullable=True),
        sa.Column("amount_paid_fcfa", sa.Integer(), nullable=False),
        sa.Column("payment_reference", sa.String(length=128), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["granted_by_admin_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plan_id"], ["license_plans.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_license_grants_user_id", "user_license_grants", ["user_id"], unique=False)

    op.create_table(
        "user_module_entitlements",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("module_key", sa.String(length=32), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "module_key"),
    )

    op.create_table(
        "user_ai_quotas",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("credits_total", sa.Integer(), nullable=False),
        sa.Column("credits_used", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "projects",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("locked_title", sa.String(length=512), nullable=False),
        sa.Column("locked_author_name", sa.String(length=255), nullable=False),
        sa.Column("locked_matricule", sa.String(length=64), nullable=False),
        sa.Column("topic_locked", sa.Boolean(), nullable=False),
        sa.Column("data_json", JSON_VARIANT, nullable=False),
        sa.Column("sections_validated_count", sa.Integer(), nullable=False),
        sa.Column("patients_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_index("ix_projects_owner_user_id", "projects", ["owner_user_id"], unique=False)

    op.create_table(
        "presentations",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("slides_count", sa.Integer(), nullable=False),
        sa.Column("data_json", JSON_VARIANT, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_index("ix_presentations_owner_user_id", "presentations", ["owner_user_id"], unique=False)

    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("module_key", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("model_used", sa.String(length=128), nullable=False),
        sa.Column("credits_cost", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_events_user_id", "usage_events", ["user_id"], unique=False)
    op.create_index("ix_usage_events_project_id", "usage_events", ["project_id"], unique=False)
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"], unique=False)

    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("admin_id", sa.String(length=36), nullable=True),
        sa.Column("target_user_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("details_json", JSON_VARIANT, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["admin_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_audit_logs_admin_id", "admin_audit_logs", ["admin_id"], unique=False)
    op.create_index("ix_admin_audit_logs_target_user_id", "admin_audit_logs", ["target_user_id"], unique=False)


def downgrade() -> None:
    op.drop_table("admin_audit_logs")
    op.drop_table("usage_events")
    op.drop_table("presentations")
    op.drop_table("projects")
    op.drop_table("user_ai_quotas")
    op.drop_table("user_module_entitlements")
    op.drop_table("user_license_grants")
    op.drop_table("license_plans")
    op.drop_table("users")
