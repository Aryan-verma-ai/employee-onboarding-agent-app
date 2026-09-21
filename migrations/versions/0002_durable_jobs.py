"""Durable extraction queue with forced tenant RLS."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "extraction_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(100), nullable=False),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("onboarding_cases.id"), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("error_code", sa.String(50)),
    )
    op.create_index("ix_extraction_jobs_tenant_id", "extraction_jobs", ["tenant_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE extraction_jobs ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE extraction_jobs FORCE ROW LEVEL SECURITY")
        condition = "tenant_id = current_setting('app.tenant_id', true) AND EXISTS (SELECT 1 FROM onboarding_cases c WHERE c.id = extraction_jobs.case_id)"
        op.execute(
            f"CREATE POLICY scoped_access ON extraction_jobs USING ({condition}) WITH CHECK ({condition})"
        )


def downgrade():
    op.drop_table("extraction_jobs")
