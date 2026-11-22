"""Months the user has closed: what the month's books said at that moment."""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE month_closes (
            month      TEXT PRIMARY KEY CHECK (month GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'),
            closed_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            currency   TEXT NOT NULL,
            income     INTEGER NOT NULL,   -- minor units of `currency`
            expenses   INTEGER NOT NULL
        ) STRICT
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE month_closes")
