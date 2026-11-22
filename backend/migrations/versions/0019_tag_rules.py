"""Tag rules: lines whose merchant text contains a pattern get a tag by themselves, on import
and when the rule is made (the same matching as category rules)."""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE tag_rules (
            id      INTEGER PRIMARY KEY,
            pattern TEXT NOT NULL,
            tag     TEXT NOT NULL,
            UNIQUE (pattern, tag)
        ) STRICT
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE tag_rules")
