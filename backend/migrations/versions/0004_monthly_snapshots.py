"""Monthly report snapshots (what the month looked like when it closed).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-21
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE monthly_snapshots (
            month      TEXT PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            report     TEXT NOT NULL CHECK (json_valid(report))
        ) STRICT
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE monthly_snapshots")
