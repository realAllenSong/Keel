"""Track where a transaction's category came from.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-20
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE transactions ADD COLUMN category_source TEXT CHECK (category_source IS NULL"
        " OR category_source IN ('user','override','rule','seed','llm','hint','transfer'))"
    )
    op.execute("CREATE INDEX ix_txn_status ON transactions(status)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_txn_status")
    op.execute("ALTER TABLE transactions DROP COLUMN category_source")
