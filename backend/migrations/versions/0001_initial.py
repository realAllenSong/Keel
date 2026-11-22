"""Initial double-entry ledger schema.

Revision ID: 0001
Revises:
Create Date: 2026-10-20

Invariants enforced by the database itself:
  * every *sealed* transaction has >= 2 postings whose weights sum to exactly 0
  * postings of a sealed transaction cannot be inserted, deleted, or have amounts changed
    (re-categorising = changing posting.account_id, which stays allowed)
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DDL = r"""
CREATE TABLE commodities (
    code        TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('currency','security','crypto','other')),
    precision   INTEGER NOT NULL CHECK (precision BETWEEN 0 AND 12),
    name        TEXT
) STRICT;

CREATE TABLE accounts (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,              -- e.g. Assets:Chase:Checking
    type        TEXT NOT NULL CHECK (type IN ('Assets','Liabilities','Income','Expenses','Equity')),
    currency    TEXT REFERENCES commodities(code),
    institution TEXT,
    asset_class TEXT CHECK (asset_class IS NULL OR asset_class IN
                  ('cash','brokerage','retirement','crypto','real_estate','vehicle','other')),
    opened_on   TEXT,
    closed_on   TEXT,
    note        TEXT,
    CHECK (substr(name, 1, length(type)) = type)
) STRICT;

CREATE TABLE import_files (
    id           INTEGER PRIMARY KEY,
    sha256       TEXT NOT NULL UNIQUE,
    filename     TEXT NOT NULL,
    importer     TEXT NOT NULL,
    account_id   INTEGER REFERENCES accounts(id),
    raw_path     TEXT NOT NULL,                     -- relative to raw/
    period_start TEXT,
    period_end   TEXT,
    n_rows       INTEGER NOT NULL DEFAULT 0,
    imported_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
) STRICT;

CREATE TABLE transactions (
    id               INTEGER PRIMARY KEY,
    date             TEXT NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
    description      TEXT NOT NULL DEFAULT '',
    payee            TEXT,
    kind             TEXT NOT NULL DEFAULT 'normal'
                       CHECK (kind IN ('normal','transfer','opening','adjustment','trade','manual')),
    status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed')),
    weight_commodity TEXT NOT NULL REFERENCES commodities(code),
    import_file_id   INTEGER REFERENCES import_files(id),
    note             TEXT,
    sealed           INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0,1)),
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
) STRICT;
CREATE INDEX ix_txn_date ON transactions(date);
CREATE INDEX ix_txn_file ON transactions(import_file_id);

CREATE TABLE postings (
    id          INTEGER PRIMARY KEY,
    txn_id      INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    amount      INTEGER NOT NULL,                    -- minor units of `commodity`
    commodity   TEXT NOT NULL REFERENCES commodities(code),
    weight      INTEGER NOT NULL,                    -- minor units of txn.weight_commodity
    price       TEXT,                                -- decimal string, weight per unit, if converted
    source_key  TEXT,                                -- stable dedup key of the statement line
    memo        TEXT
) STRICT;
CREATE INDEX ix_post_txn ON postings(txn_id);
CREATE INDEX ix_post_account ON postings(account_id, commodity);
CREATE UNIQUE INDEX ux_post_source ON postings(account_id, source_key) WHERE source_key IS NOT NULL;

CREATE TABLE balance_assertions (
    id             INTEGER PRIMARY KEY,
    account_id     INTEGER NOT NULL REFERENCES accounts(id),
    date           TEXT NOT NULL,                    -- balance at END of this date
    amount         INTEGER NOT NULL,
    commodity      TEXT NOT NULL REFERENCES commodities(code),
    import_file_id INTEGER REFERENCES import_files(id) ON DELETE CASCADE,
    UNIQUE (account_id, date, commodity, import_file_id)
) STRICT;

CREATE TABLE prices (
    date    TEXT NOT NULL,
    base    TEXT NOT NULL,                           -- 1 unit of base ...
    quote   TEXT NOT NULL,                           -- ... costs `rate` units of quote
    rate    TEXT NOT NULL,                           -- decimal string
    source  TEXT NOT NULL,
    PRIMARY KEY (date, base, quote)
) STRICT;

CREATE TABLE lots (
    id            INTEGER PRIMARY KEY,
    account_id    INTEGER NOT NULL REFERENCES accounts(id),
    commodity     TEXT NOT NULL REFERENCES commodities(code),
    open_txn_id   INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    open_date     TEXT NOT NULL,
    quantity      INTEGER NOT NULL,                  -- remaining, minor units of commodity
    cost_per_unit TEXT NOT NULL,                     -- decimal string in cost_commodity
    cost_commodity TEXT NOT NULL REFERENCES commodities(code)
) STRICT;

-- ---- User decisions: survive `pf rebuild` ----
CREATE TABLE rules (
    id             INTEGER PRIMARY KEY,
    priority       INTEGER NOT NULL DEFAULT 100,
    field          TEXT NOT NULL DEFAULT 'description' CHECK (field IN ('description','payee')),
    match_type     TEXT NOT NULL DEFAULT 'contains' CHECK (match_type IN ('contains','regex','exact')),
    pattern        TEXT NOT NULL,
    account_filter TEXT,                             -- only for lines from accounts matching this prefix
    amount_min     INTEGER,
    amount_max     INTEGER,
    target_account TEXT NOT NULL,                    -- account name (resolved/created on apply)
    payee          TEXT,                             -- optional clean payee name
    source         TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('user','llm','seed')),
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
) STRICT;

CREATE TABLE overrides (                             -- one-off categorisation of a specific line
    account_name   TEXT NOT NULL,
    source_key     TEXT NOT NULL,
    target_account TEXT NOT NULL,
    payee          TEXT,
    PRIMARY KEY (account_name, source_key)
) STRICT;

-- ---- Integrity triggers ----
CREATE TRIGGER trg_seal_requires_balance
BEFORE UPDATE OF sealed ON transactions
WHEN NEW.sealed = 1 AND OLD.sealed = 0
BEGIN
    SELECT RAISE(ABORT, 'unbalanced transaction: postings weights must sum to 0')
    WHERE (SELECT COALESCE(SUM(weight), 0) FROM postings WHERE txn_id = NEW.id) != 0;
    SELECT RAISE(ABORT, 'transaction needs at least 2 postings')
    WHERE (SELECT COUNT(*) FROM postings WHERE txn_id = NEW.id) < 2;
END;

CREATE TRIGGER trg_no_insert_into_sealed
BEFORE INSERT ON postings
WHEN (SELECT sealed FROM transactions WHERE id = NEW.txn_id) = 1
BEGIN
    SELECT RAISE(ABORT, 'cannot add postings to a sealed transaction');
END;

CREATE TRIGGER trg_no_delete_from_sealed
BEFORE DELETE ON postings
WHEN (SELECT sealed FROM transactions WHERE id = OLD.txn_id) = 1
BEGIN
    SELECT RAISE(ABORT, 'cannot delete postings of a sealed transaction (unseal first)');
END;

CREATE TRIGGER trg_amounts_locked_when_sealed
BEFORE UPDATE OF amount, weight, commodity, txn_id ON postings
WHEN (SELECT sealed FROM transactions WHERE id = OLD.txn_id) = 1
BEGIN
    SELECT RAISE(ABORT, 'amounts of a sealed transaction are immutable');
END;

CREATE TRIGGER trg_no_insert_sealed
BEFORE INSERT ON transactions
WHEN NEW.sealed = 1
BEGIN
    SELECT RAISE(ABORT, 'insert unsealed, add postings, then seal');
END;

-- ---- Views ----
CREATE VIEW v_postings AS
SELECT p.id, p.txn_id, t.date, t.description, t.payee, t.kind, t.status,
       a.id AS account_id, a.name AS account, a.type AS account_type, a.asset_class,
       p.amount, p.commodity, c.precision, p.weight, t.weight_commodity, p.source_key
FROM postings p
JOIN transactions t ON t.id = p.txn_id AND t.sealed = 1
JOIN accounts a ON a.id = p.account_id
JOIN commodities c ON c.code = p.commodity;

CREATE VIEW v_unbalanced AS
SELECT t.id, t.date, t.description, COALESCE(SUM(p.weight), 0) AS residual, COUNT(p.id) AS n
FROM transactions t LEFT JOIN postings p ON p.txn_id = t.id
GROUP BY t.id
HAVING t.sealed = 0 OR residual != 0 OR n < 2;

INSERT INTO commodities(code, kind, precision, name) VALUES
    ('USD','currency',2,'US Dollar'),
    ('CNY','currency',2,'Chinese Yuan'),
    ('HKD','currency',2,'Hong Kong Dollar'),
    ('EUR','currency',2,'Euro'),
    ('JPY','currency',0,'Japanese Yen'),
    ('BTC','crypto',8,'Bitcoin'),
    ('ETH','crypto',8,'Ether');
"""


def _statements(sql: str) -> list[str]:
    """Split on ';' at statement level while keeping trigger bodies (BEGIN ... END;) intact."""
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    for line in sql.splitlines():
        stripped = line.strip().upper()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.startswith("CREATE TRIGGER"):
            depth = 1
        if depth and stripped == "END;":
            depth = 0
            out.append("\n".join(buf))
            buf = []
            continue
        if not depth and stripped.endswith(";"):
            out.append("\n".join(buf))
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        out.append(tail)
    return [s for s in (x.strip() for x in out) if s]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.execute(stmt)


def downgrade() -> None:
    raise NotImplementedError("initial schema cannot be downgraded; restore from a backup")
