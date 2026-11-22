"""Category source 'learned': suggested by the local classifier from your own confirmed lines.

SQLite cannot change a CHECK in place; the column is swapped (no index, view or trigger uses it)."""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

OLD = "'user','override','rule','seed','llm','hint','transfer'"


def _swap(values: str, keep: str) -> None:
    op.execute(
        "ALTER TABLE transactions ADD COLUMN category_source_new TEXT CHECK"
        f" (category_source_new IS NULL OR category_source_new IN ({values}))"
    )
    op.execute(f"UPDATE transactions SET category_source_new = {keep}")
    op.execute("ALTER TABLE transactions DROP COLUMN category_source")
    op.execute("ALTER TABLE transactions RENAME COLUMN category_source_new TO category_source")


def upgrade() -> None:
    _swap(OLD + ",'learned'", "category_source")


def downgrade() -> None:
    _swap(OLD, "CASE WHEN category_source = 'learned' THEN 'hint' ELSE category_source END")
