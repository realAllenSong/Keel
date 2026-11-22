"""Saved simulation scenarios.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE scenarios (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL UNIQUE,
            body       TEXT NOT NULL CHECK (json_valid(body)),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        ) STRICT
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE scenarios")
