"""Your edits to imported statement lines: a different date, amount, description or payee, a split
across categories, or deletion. Keyed like overrides (account + the line's stable source key), so
`pf rebuild` re-imports the statement and applies them again.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-25
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE line_edits (
            account_name TEXT NOT NULL,
            source_key   TEXT NOT NULL,
            deleted      INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
            date         TEXT,
            amount       TEXT,           -- decimal string, ledger sign of the statement account
            description  TEXT,
            payee        TEXT,
            note         TEXT,
            splits       TEXT,           -- JSON [[category, amount], ...]: amounts as spent (+)
            PRIMARY KEY (account_name, source_key)
        ) STRICT
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE line_edits")
