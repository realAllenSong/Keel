from __future__ import annotations

from alembic import context

from pf.db import make_engine

connection = context.config.attributes.get("connection")


def run(conn) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=conn, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


if connection is not None:
    run(connection)
else:
    with make_engine().connect() as conn:
        run(conn)
