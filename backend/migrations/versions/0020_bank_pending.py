"""In flight: what a bank shows but has not posted yet, kept beside the ledger.

bank_pending holds a connection's pending lines (a card swipe, a deposit hold, an incoming
transfer) as of its latest sync; bank_available holds the bank's available and current balance
for cash accounts, whose difference is money on its way in (or held). Neither is ever booked:
net worth, reports, reconciliation and month close count posted lines only. Each sync replaces
an account's rows, and `pf rebuild` refills both by replaying the same raw statements."""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE bank_pending (
            id            INTEGER PRIMARY KEY,
            account_id    INTEGER NOT NULL REFERENCES accounts(id),
            ref           TEXT NOT NULL,
            date          TEXT NOT NULL,
            amount        TEXT NOT NULL,          -- ledger sign: money out is negative
            currency      TEXT NOT NULL,
            description   TEXT NOT NULL,
            payee         TEXT,
            category_hint TEXT,
            kind          TEXT NOT NULL CHECK (kind IN ('spend', 'income', 'transfer')),
            hold          INTEGER NOT NULL DEFAULT 0,  -- a deposit or pre-authorisation
            as_of         TEXT NOT NULL,
            UNIQUE (account_id, ref)
        ) STRICT
        """
    )
    op.execute(
        """
        CREATE TABLE bank_available (
            account_id INTEGER PRIMARY KEY REFERENCES accounts(id),
            as_of      TEXT NOT NULL,
            current    TEXT NOT NULL,
            available  TEXT NOT NULL
        ) STRICT
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE bank_available")
    op.execute("DROP TABLE bank_pending")
