from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import text

from pf.db import migrate, transaction
from pf.importers.base import ImportError_
from pf.money import parse_decimal

app = typer.Typer(no_args_is_help=True, help="Personal finance ledger CLI")


@app.callback()
def main() -> None:
    """Personal finance ledger CLI."""


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8711, reload: bool = False) -> None:
    """Run the FastAPI server."""
    import uvicorn

    uvicorn.run("pf.api.app:app", host=host, port=port, reload=reload)


@app.command()
def init() -> None:
    """Create or upgrade the ledger database schema."""
    migrate()
    typer.echo("database ready")


@app.command("import")
def import_(
    files: list[Path],
    account: str | None = typer.Option(None, help="Ledger account, e.g. Assets:Chase:Checking"),
    importer: str | None = typer.Option(None, help="Force an importer profile id"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Import statement files (each file is atomic: all of it or nothing)."""
    from pf.ledger.importing import import_file

    migrate()
    failed = False
    for f in files:
        try:
            with transaction() as conn:
                r = import_file(conn, f, account=account, importer=importer)
        except (ImportError_, ValueError) as e:
            failed = True
            typer.secho(f"✗ {f.name}: {e}", fg="red", err=True)
            continue
        if as_json:
            typer.echo(json.dumps(asdict(r), default=str))
            continue
        if r.status in ("needs_account", "unknown_format"):
            typer.echo(f"· {f.name}: 待认领账户或识别格式 [{r.pending_sha}]")
            continue
        if r.status == "already_imported":
            typer.echo(f"· {f.name}: already imported")
            continue
        typer.echo(
            f"✓ {f.name} → {r.account} [{r.importer}] {r.period[0] if r.period else ''}"
            f"..{r.period[1] if r.period else ''}: {r.new} new, {r.duplicates} duplicates"
            + (f", opening balance {r.opening_balance}" if r.opening_balance else "")
        )
        for d in r.discrepancies:
            typer.secho(
                f"  ⚠ {d.date} statement {d.statement} ≠ ledger {d.ledger} (diff {d.diff})",
                fg="yellow",
            )
    raise typer.Exit(1 if failed else 0)


@app.command()
def reconcile(account: str | None = None) -> None:
    """Compare every statement balance with the ledger."""
    from pf.ledger.reconcile import reconcile as run

    with transaction() as conn:
        issues = run(conn, account)
    if not issues:
        typer.secho("✓ all balance assertions reconcile", fg="green")
        return
    for d in issues:
        typer.secho(
            f"✗ {d.account} {d.date}: statement {d.statement} {d.commodity}, "
            f"ledger {d.ledger} (diff {d.diff})",
            fg="red",
        )
    raise typer.Exit(1)


@app.command()
def check() -> None:
    """Integrity check: unbalanced transactions, broken FKs, assertions."""
    with transaction() as conn:
        unbalanced = conn.execute(text("SELECT COUNT(*) FROM v_unbalanced")).scalar_one()
        fk = conn.execute(text("PRAGMA foreign_key_check")).all()
        integrity = conn.execute(text("PRAGMA integrity_check")).scalar_one()
    ok = unbalanced == 0 and not fk and integrity == "ok"
    typer.echo(f"unbalanced transactions: {unbalanced}")
    typer.echo(f"foreign key violations: {len(fk)}")
    typer.echo(f"sqlite integrity: {integrity}")
    if not ok:
        raise typer.Exit(1)
    reconcile(None)


@app.command()
def rebuild(yes: bool = typer.Option(False, "--yes")) -> None:
    """Re-derive the ledger from raw/ + rules/overrides (atomic)."""
    from pf.ledger.rebuild import rebuild as run

    if not yes:
        typer.confirm("Rebuild all imported transactions from raw/?", abort=True)
    with transaction() as conn:
        reports = run(conn)
    typer.echo(f"rebuilt from {len(reports)} files, {sum(r.new for r in reports)} transactions")


@app.command("assert")
def assert_balance(account: str, date: str, amount: str, commodity: str = "USD") -> None:
    """Record a statement balance (ledger sign: card debt is negative)."""
    from pf.ledger.balances import assert_balance as record

    with transaction() as conn:
        record(conn, account, dt.date.fromisoformat(date), parse_decimal(amount), commodity)
    reconcile(account)


@app.command()
def value(
    account: str = typer.Argument(..., help="e.g. Assets:Vanguard:Brokerage"),
    date: str = typer.Argument(..., help="YYYY-MM-DD"),
    amount: str = typer.Argument(..., help="market value on that date"),
    commodity: str = "USD",
) -> None:
    """Record an account's market value on a date (regenerates valuation adjustments)."""
    from pf.ledger.valuation import set_value

    migrate()
    with transaction() as conn:
        set_value(conn, account, dt.date.fromisoformat(date), parse_decimal(amount), commodity)
    typer.echo(f"✓ {account} = {amount} {commodity} on {date}")


decisions_app = typer.Typer(help="Export/import rules, overrides and valuations (YAML)")
app.add_typer(decisions_app, name="decisions")


@decisions_app.command("export")
def decisions_export(path: Path) -> None:
    import yaml

    from pf.ledger.decisions import export_decisions

    with transaction() as conn:
        data = export_decisions(conn)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    typer.echo(
        f"✓ {len(data['rules'])} rules, {len(data['overrides'])} overrides,"
        f" {len(data['valuations'])} valuations → {path}"
    )


@decisions_app.command("import")
def decisions_import(path: Path) -> None:
    import yaml

    from pf.ledger.decisions import import_decisions

    migrate()
    with transaction() as conn:
        n = import_decisions(conn, yaml.safe_load(path.read_text(encoding="utf-8")))
    typer.echo(f"✓ imported {n}")


@app.command()
def accounts() -> None:
    """List accounts with current balances."""
    with transaction() as conn:
        rows = conn.execute(
            text(
                "SELECT account, commodity, precision, SUM(amount) AS bal FROM v_postings"
                " GROUP BY account, commodity ORDER BY account"
            )
        ).all()
    for r in rows:
        from pf.money import from_minor

        typer.echo(f"{r.account:<45} {from_minor(r.bal, r.precision):>14,} {r.commodity}")


@app.command()
def rename(old: str, new: str) -> None:
    """Rename an account (and every rule, fix and valuation that names it)."""
    from pf.ledger.rename import rename_account

    with transaction() as conn:
        rename_account(conn, old, new)
    typer.echo(f"✓ {old} → {new}")


@app.command("tidy-names")
def tidy_names() -> None:
    """Rename accounts created by bank / broker connections to the short scheme (Card-0002)."""
    from pf.ledger.rename import tidy_connector_names

    with transaction() as conn:
        done = tidy_connector_names(conn)
    for old, new in done:
        typer.echo(f"✓ {old} → {new}")
    if not done:
        typer.echo("Nothing to rename.")


# ---------------------------------------------------------------- automation (M6)
@app.command()
def watch() -> None:
    """Watch configured download directories and the Inbox (runs forever)."""
    from pf.ops.inbox import watch as run

    typer.echo("正在监视下载目录和收件箱…（Ctrl+C 停止）")
    run()


@app.command()
def inbox() -> None:
    """Scan every watched folder once (Downloads read-only, Finance-Inbox archived)."""
    from pf.ops.inbox import scan_watch_dirs

    r = scan_watch_dirs(wait_settle=False)
    for rep in r.imported:
        if rep.status == "imported":
            typer.echo(f"✓ {rep.file}: {rep.new} new, {rep.duplicates} duplicates")
        else:
            typer.echo(f"? {rep.file}: {rep.status} — finish it in Keel (首页 → 需要你做的事)")
    for name in r.skipped:
        typer.echo(f"· {name}: skipped")
    for name, err in r.failed:
        typer.secho(f"✗ {name}: {err}", fg="red")


@app.command()
def market(
    history: Annotated[
        bool, typer.Option("--history", help="also rebuild the 1871+ monthly history")
    ] = False,
) -> None:
    """Fetch FX (CNY) and macro series (T-bill, CPI) from FRED; --history rebuilds sim history."""
    from pf.market.update import update_market

    if history:
        from pf.market.history import refresh_history

        h = refresh_history()
        typer.echo(f"history {h.df.index.min()} → {h.df.index.max()} ({len(h.df)} months)")
    migrate()
    with transaction() as conn:
        r = update_market(conn)
    typer.echo(f"fx rows {r.fx_rows}, macro {r.macro}")
    for e in r.errors:
        typer.secho(f"⚠ {e}", fg="yellow", err=True)


backup_app = typer.Typer(help="Verified, compressed ledger snapshots (iCloud Drive by default)")
app.add_typer(backup_app, name="backup")


@backup_app.command("run")
def backup_run() -> None:
    from pf.ops.backup import backup_dir, run_backup

    r = run_backup()
    what = f"wrote {r.snapshot.name}" if r.snapshot else "unchanged, skipped"
    typer.echo(
        f"{backup_dir()}: {what}; kept {r.kept}, pruned {r.pruned}, raw files copied {r.raw_copied}"
    )


@backup_app.command("list")
def backup_list() -> None:
    from pf.ops.backup import PREFIX, SUFFIX, backup_dir

    for p in sorted(backup_dir().glob(f"{PREFIX}*{SUFFIX}")):
        typer.echo(f"{p.name}  {p.stat().st_size / 1024:,.0f} KB")


@backup_app.command("restore")
def backup_restore(snapshot: Path, to: Path) -> None:
    """Restore a snapshot to a NEW file (never overwrites)."""
    from pf.ops.backup import restore

    typer.echo(f"restored → {restore(snapshot, to)} (integrity verified)")


@app.command()
def sync(
    source: str | None = typer.Argument(None, help="plaid | snaptrade | ibkr_flex | okx"),
) -> None:
    """Pull new transactions, balances and values from the accounts linked in Settings → 连接."""
    sync_accounts(source)


def sync_accounts(source: str | None = None) -> None:
    from pf.connectors import CONNECTORS, run_sync, sync_all

    with transaction() as conn:
        results = [run_sync(conn, CONNECTORS[source])] if source else sync_all(conn)
    for r in results:
        mark = "✓" if r.ok else "✗"
        typer.echo(f"{mark} {r.source}: {r.accounts} accounts, {r.new} new {r.message or ''}")
    if not results:
        typer.echo("No connections yet (Settings → 连接).")
    if any(not r.ok for r in results):
        raise typer.Exit(1)


@app.command()
def daily() -> None:
    """Daily job: market data, account sync, backup, then new alerts. Each step runs even if
    another fails."""
    ok = True
    for step in (market, sync_accounts, backup_run, alerts):
        try:
            step()
        except Exception as e:  # keep the schedule alive; surface in logs
            ok = False
            typer.secho(f"✗ {step.__name__}: {e}", fg="red", err=True)
    if not ok:
        from pf.i18n import tr
        from pf.ops.notify import notify
        from pf.prefs import use_saved_language

        with transaction() as conn:
            use_saved_language(conn)
        notify(tr("每日任务有错误"), tr("查看 data/logs/daily.log"))
        raise typer.Exit(1)


@app.command()
def alerts() -> None:
    """Notify new alerts (spending pace, missing paycheck, low cash, renewals) once each."""
    from pf.ops.alerts import notify_new_alerts

    migrate()
    with transaction() as conn:
        sent = notify_new_alerts(conn)
    typer.echo(f"{len(sent)} new alert(s)")


@app.command()
def monthly(month: str | None = typer.Option(None, help="YYYY-MM, default: last month")) -> None:
    """Close a month: snapshot its report and send a notification."""
    from pf.ops.monthly import previous_month, snapshot_month, summary_line
    from pf.ops.notify import notify

    m = month or previous_month()
    migrate()
    from pf.i18n import tr
    from pf.prefs import use_saved_language

    with transaction() as conn:
        use_saved_language(conn)
        report = snapshot_month(conn, m)
    line = summary_line(report)
    typer.echo(f"{m}: {line}")
    notify(tr("{month} 月报已生成", month=m), line)


agents_app = typer.Typer(help="launchd agents: api, inbox watcher, daily, monthly")
app.add_typer(agents_app, name="agents")


@agents_app.command("install")
def agents_install(
    only: Annotated[list[str] | None, typer.Option("--only", help="install only these")] = None,
) -> None:
    from pf.ops import agents

    typer.echo(f"installed: {', '.join(agents.install(only or None))}")


@agents_app.command("uninstall")
def agents_uninstall() -> None:
    from pf.ops import agents

    typer.echo(f"removed: {', '.join(agents.uninstall()) or 'nothing'}")


@agents_app.command("status")
def agents_status() -> None:
    from pf.ops import agents

    for name, state in agents.status().items():
        typer.echo(f"{name:<8} {state}")


if __name__ == "__main__":
    app()
