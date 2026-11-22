"""Names that are you. A wallet transfer to one of them moves your own money (to your other
Alipay or WeChat login) instead of paying someone. Names come from the statements' headers
(source 'statement', re-derived on rebuild) or from you (source 'user', kept as a decision).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-25
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE owner_names ("
        " name TEXT PRIMARY KEY,"
        " source TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('user', 'statement'))"
        ") STRICT"
    )


def downgrade() -> None:
    op.execute("DROP TABLE owner_names")
