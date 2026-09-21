"""Record consent withdrawal and explain validation decisions."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "onboarding_cases",
        sa.Column("consent_policy_version", sa.String(100), nullable=False, server_default="1"),
    )
    op.add_column(
        "onboarding_cases", sa.Column("consent_withdrawn_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "onboarding_cases", sa.Column("validation_outcomes", sa.JSON(), nullable=False, server_default="[]")
    )


def downgrade():
    op.drop_column("onboarding_cases", "validation_outcomes")
    op.drop_column("onboarding_cases", "consent_withdrawn_at")
    op.drop_column("onboarding_cases", "consent_policy_version")
