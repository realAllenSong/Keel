"""Persist account claims, learned formats and read-only download scan decisions."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE account_bindings (
        importer TEXT NOT NULL, hint TEXT NOT NULL DEFAULT '', account_name TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        PRIMARY KEY (importer, hint)
    ) STRICT""")
    op.execute("""CREATE TABLE user_profiles (
        id TEXT PRIMARY KEY, body TEXT NOT NULL CHECK (json_valid(body)),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    ) STRICT""")
    op.execute("""CREATE TABLE pending_imports (
        sha256 TEXT PRIMARY KEY, filename TEXT NOT NULL, stored_path TEXT NOT NULL,
        reason TEXT NOT NULL CHECK (reason IN ('needs_account','unknown_format')),
        importer TEXT, institution TEXT, account_type TEXT, hint TEXT,
        preview TEXT CHECK (preview IS NULL OR json_valid(preview)),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    ) STRICT""")
    op.execute("""CREATE TABLE seen_files (
        sha256 TEXT PRIMARY KEY, path TEXT NOT NULL, outcome TEXT NOT NULL,
        seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    ) STRICT""")
    op.execute("""CREATE TABLE app_settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL CHECK (json_valid(value))
    ) STRICT""")


def downgrade() -> None:
    for table in (
        "app_settings",
        "seen_files",
        "pending_imports",
        "user_profiles",
        "account_bindings",
    ):
        op.execute(f"DROP TABLE {table}")
