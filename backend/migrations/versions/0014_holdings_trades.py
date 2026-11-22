"""What a brokerage account holds and what was traded in it.

The ledger books a brokerage account as one value (marks) plus the cash that crosses its edge.
These two tables keep the detail a broker reports beside that: positions per sync, and every
buy, sell, reinvestment, split and option event. They are read models: nothing in net worth or
income depends on them, and `pf rebuild` refills them from the same raw statements.
Quantities and prices are decimal strings (fractional shares, option prices)."""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE holding_snapshots (
            account_id     INTEGER NOT NULL REFERENCES accounts(id),
            as_of          TEXT NOT NULL,              -- the day the broker reported
            import_file_id INTEGER REFERENCES import_files(id) ON DELETE CASCADE,
            PRIMARY KEY (account_id, as_of)
        ) STRICT
        """
    )
    op.execute(
        """
        CREATE TABLE holdings (
            id              INTEGER PRIMARY KEY,
            account_id      INTEGER NOT NULL,
            as_of           TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            kind            TEXT,                      -- stock | etf | fund | option | ...
            quantity        TEXT NOT NULL,
            price           TEXT,                      -- per unit
            cost_basis      TEXT,                      -- of the whole position
            currency        TEXT NOT NULL,
            cash_equivalent INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (account_id, as_of) REFERENCES holding_snapshots(account_id, as_of)
                ON DELETE CASCADE,
            UNIQUE (account_id, as_of, symbol)
        ) STRICT
        """
    )
    op.execute(
        """
        CREATE TABLE trades (
            id             INTEGER PRIMARY KEY,
            account_id     INTEGER NOT NULL REFERENCES accounts(id),
            date           TEXT NOT NULL,              -- trade date
            action         TEXT NOT NULL,              -- BUY SELL REINVEST SPLIT ASSIGNMENT ...
            symbol         TEXT NOT NULL,              -- an option's own ticker for options
            underlying     TEXT,                       -- options: the stock it is on
            kind           TEXT,
            quantity       TEXT NOT NULL,              -- signed: + adds to the position
            price          TEXT,
            amount         TEXT NOT NULL,              -- cash effect, + = cash in
            fee            TEXT,
            currency       TEXT NOT NULL,
            description    TEXT,
            ref            TEXT NOT NULL,              -- the broker's id for this event
            import_file_id INTEGER REFERENCES import_files(id) ON DELETE CASCADE,
            UNIQUE (account_id, ref)
        ) STRICT
        """
    )
    op.execute("CREATE INDEX ix_trades_account_date ON trades(account_id, date)")


def downgrade() -> None:
    op.execute("DROP TABLE trades")
    op.execute("DROP TABLE holdings")
    op.execute("DROP TABLE holding_snapshots")
