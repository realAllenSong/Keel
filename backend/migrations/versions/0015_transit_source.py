"""Category source 'transit': a card payment booked from the bank's side as a transfer to the
card before the card has listed it (pf/ledger/transfers.py). When the card's line arrives the
two halves merge into an ordinary paired transfer.

SQLite cannot change a CHECK in place; the column is swapped (no index, view or trigger uses it)."""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

OLD = "'user','override','rule','seed','llm','hint','transfer','learned'"


def _swap(values: str, keep: str) -> None:
    op.execute(
        "ALTER TABLE transactions ADD COLUMN category_source_new TEXT CHECK"
        f" (category_source_new IS NULL OR category_source_new IN ({values}))"
    )
    op.execute(f"UPDATE transactions SET category_source_new = {keep}")
    op.execute("ALTER TABLE transactions DROP COLUMN category_source")
    op.execute("ALTER TABLE transactions RENAME COLUMN category_source_new TO category_source")


def upgrade() -> None:
    _swap(OLD + ",'transit'", "category_source")


def downgrade() -> None:
    _swap(OLD, "CASE WHEN category_source = 'transit' THEN 'transfer' ELSE category_source END")
