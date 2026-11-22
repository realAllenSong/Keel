"""Savings goals: a target amount, optionally a date, and the accounts whose money counts toward
it (none = net worth). The user's own plans, not derived from statements: a rebuild keeps them."""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE goals (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            target     TEXT NOT NULL,              -- decimal string, in `currency`
            currency   TEXT NOT NULL,
            due        TEXT,                       -- the day it should be reached, if any
            accounts   TEXT NOT NULL DEFAULT '[]', -- JSON list of account names; [] = net worth
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d','now'))
        ) STRICT
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE goals")
