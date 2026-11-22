"""Tags on transactions ("japan-trip", "move"): a second way to group lines, across categories.

Like every decision about an imported line, a tag is stored under the line's account and source
key, so `pf rebuild` gives the same ledger. A hand-entered transaction has no statement line;
its tags are stored under ('', 'manual:<transaction id>')."""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE line_tags (
            tag          TEXT NOT NULL,
            account_name TEXT NOT NULL,
            source_key   TEXT NOT NULL,
            PRIMARY KEY (tag, account_name, source_key)
        ) STRICT
        """
    )
    op.execute("CREATE INDEX ix_line_tags_line ON line_tags(account_name, source_key)")


def downgrade() -> None:
    op.execute("DROP TABLE line_tags")
