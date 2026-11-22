"""Password-protected statement zips (Alipay / WeChat Pay): a pending reason and a record of
which archive produced which imported file, so dropping the same zip again asks nothing.
`accounts.flow_only` marks wallets whose income/spending count but whose balance is not money held."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_PENDING = """CREATE TABLE {name} (
        sha256 TEXT PRIMARY KEY, filename TEXT NOT NULL, stored_path TEXT NOT NULL,
        reason TEXT NOT NULL CHECK (reason IN ({reasons})),
        importer TEXT, institution TEXT, account_type TEXT, hint TEXT,
        preview TEXT CHECK (preview IS NULL OR json_valid(preview)),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    ) STRICT"""


def _rebuild(reasons: str, keep: str) -> None:
    # SQLite cannot alter a CHECK constraint: copy into a new table and swap.
    op.execute(_PENDING.format(name="pending_imports_new", reasons=reasons))
    op.execute(f"INSERT INTO pending_imports_new SELECT * FROM pending_imports WHERE {keep}")
    op.execute("DROP TABLE pending_imports")
    op.execute("ALTER TABLE pending_imports_new RENAME TO pending_imports")


def upgrade() -> None:
    _rebuild("'needs_account','unknown_format','needs_password'", "1")
    op.execute(
        "ALTER TABLE accounts ADD COLUMN flow_only INTEGER NOT NULL DEFAULT 0"
        " CHECK (flow_only IN (0, 1))"
    )
    op.execute("""CREATE TABLE archive_imports (
        sha256 TEXT PRIMARY KEY, filename TEXT NOT NULL, inner_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    ) STRICT""")


def downgrade() -> None:
    op.execute("DROP TABLE archive_imports")
    op.execute("ALTER TABLE accounts DROP COLUMN flow_only")
    _rebuild("'needs_account','unknown_format'", "reason <> 'needs_password'")
