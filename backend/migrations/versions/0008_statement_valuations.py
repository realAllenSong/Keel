"""Valuations can come from a statement (a broker's positions page or net asset value) as well
as from you. Statement marks are re-derived from raw/ on rebuild, so they are not exported as
decisions, and a mark you type for the same day is never overwritten by a re-import.
Pending files remember their asset class so a brokerage is suggested as …:Brokerage, not Checking.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-25
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE valuations ADD COLUMN source TEXT NOT NULL DEFAULT 'user'"
        " CHECK (source IN ('user', 'statement'))"
    )
    op.execute("ALTER TABLE pending_imports ADD COLUMN asset_class TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE pending_imports DROP COLUMN asset_class")
    op.execute("ALTER TABLE valuations DROP COLUMN source")
