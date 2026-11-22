"""Recurring series the user said are not bills ("this is not a subscription"): never suggested
or forecast again until restored. The key is merchant|account|in-or-out."""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE recurring_dismissed (
            key        TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        ) STRICT
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE recurring_dismissed")
