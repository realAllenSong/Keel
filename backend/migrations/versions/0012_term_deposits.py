"""Allow term deposits to be classified separately from cash."""

from alembic import op
from sqlalchemy import text

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

OLD = "'cash','brokerage','retirement','crypto','real_estate','vehicle','other'"


def _swap(values: str, keep: str) -> None:
    view = (
        op.get_bind()
        .execute(text("SELECT sql FROM sqlite_schema WHERE type = 'view' AND name = 'v_postings'"))
        .scalar_one()
    )
    op.execute("DROP VIEW v_postings")
    op.execute(
        "ALTER TABLE accounts ADD COLUMN asset_class_new TEXT CHECK"
        f" (asset_class_new IS NULL OR asset_class_new IN ({values}))"
    )
    op.execute(f"UPDATE accounts SET asset_class_new = {keep}")
    op.execute("ALTER TABLE accounts DROP COLUMN asset_class")
    op.execute("ALTER TABLE accounts RENAME COLUMN asset_class_new TO asset_class")
    op.execute(view)


def upgrade() -> None:
    _swap(OLD + ",'term_deposit'", "asset_class")


def downgrade() -> None:
    _swap(OLD, "CASE WHEN asset_class = 'term_deposit' THEN 'other' ELSE asset_class END")
