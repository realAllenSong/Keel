"""Market-value marks (user decisions; adjustment transactions are regenerated from these).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-21
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE valuations (
            account_name TEXT NOT NULL,
            date         TEXT NOT NULL,
            value        INTEGER NOT NULL,          -- minor units of commodity
            commodity    TEXT NOT NULL REFERENCES commodities(code),
            PRIMARY KEY (account_name, date, commodity)
        ) STRICT
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE valuations")
