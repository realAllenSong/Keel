#!/usr/bin/env python3
"""A brand-new user's first hour with Keel, end to end, on synthetic statements.

Starts the engine on a fresh home (an empty ledger, its own Downloads, its own secrets file),
walks the onboarding a stranger would walk through the same API the app uses, checks what
should be true after each step, and renders the screens with the debug app in snapshot mode
(off-screen, a cleared preferences domain that is restored afterwards). Nothing touches your
real ledger, your Downloads or your keychain.

    uv run --project backend python tools/e2e_onboarding.py OUT_DIR

Run it before every release; it exits non-zero when a check fails.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "fixtures" / "demo" / "statements"
PORT = 8799  # fixed: the dev engine port used across this repo's tooling
BASE = f"http://127.0.0.1:{PORT}/api"
APP = ROOT / "apple" / ".build" / "debug" / "Keel"
DOMAIN = "Keel"  # the debug app's preferences domain (the installed app uses com.allensong.keel)

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def call(path: str, body: object | None = None, method: str | None = None) -> object:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data, method=method or ("GET" if body is None else "POST"),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def upload(path: Path) -> list[dict]:
    boundary = "keel-e2e"
    payload = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{path.name}\"\r\n"
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + "/import", data=payload, method="POST",
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def export(name: str, files: list[str], into: Path) -> Path:
    """What a bank's website gives you: one file covering the last few months."""
    out = into / name
    lines: list[str] = []
    for i, f in enumerate(files):
        rows = (DEMO / f).read_text().splitlines()
        lines += rows if i == 0 else rows[1:]
    out.write_text("\n".join(lines) + "\n")
    return out


def claim(result: dict) -> dict:
    return call(
        f"/onboarding/pending/{result['pending_sha']}/claim",
        {"account_name": result["suggested_account"]},
    )


def snapshot(out: Path, shots: str, env: dict[str, str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    e = dict(os.environ, KEEL_ENGINE_URL=f"http://127.0.0.1:{PORT}", KEEL_SNAPSHOT_DIR=str(out),
             KEEL_SNAPSHOT_OFFSCREEN="1", KEEL_SNAPSHOT_ONLY=shots, **env)
    subprocess.run([str(APP)], env=e, timeout=600, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(out: Path) -> int:
    out = out.resolve()
    home = out / "home"
    downloads = out / "Downloads"
    shutil.rmtree(out, ignore_errors=True)
    downloads.mkdir(parents=True)
    env = {
        "PF_APP_HOME": str(home),
        "PF_WATCH_DIRS": os.pathsep.join([str(downloads), str(home / "Inbox")]),
        "PF_INBOX_DIR": str(home / "Inbox"),
        "PF_SECRETS_FILE": str(home / "secrets.json"),
        "PF_NOTIFY": "0",
    }
    # First launch exactly as the packaged engine does it: folders, example config, empty bindings.
    seeded = subprocess.run(
        ["uv", "run", "--project", str(ROOT / "desktop"), "python", "-c",
         "import os,json; from ledger_app.runtime import configure; configure();"
         " print(json.dumps({k: v for k, v in os.environ.items() if k.startswith('PF_')}))"],
        cwd=ROOT / "desktop", env=dict(os.environ, **env), capture_output=True, text=True, check=True,
    )
    env.update(json.loads(seeded.stdout.strip().splitlines()[-1]))
    print("1. First launch")
    check((home / "config" / "accounts.local.yaml").exists(), "empty account bindings written")
    check(not (home / "config" / "assumptions.local.yaml").exists(), "no saved assumptions yet")

    if subprocess.run(["lsof", "-ti", f"tcp:{PORT}"], capture_output=True).stdout:
        print(f"port {PORT} is busy; stop what runs there first")
        return 2
    engine = subprocess.Popen(
        ["uv", "run", "pf", "serve", "--port", str(PORT)], cwd=ROOT / "backend",
        env=dict(os.environ, **env), stdout=open(out / "engine.log", "w"), stderr=subprocess.STDOUT,
    )
    backup = out / "debug-defaults.plist"
    subprocess.run(["defaults", "export", DOMAIN, str(backup)], capture_output=True)
    try:
        for _ in range(60):
            try:
                call("/home")
                break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.5)
        subprocess.run(["defaults", "delete", DOMAIN], capture_output=True)
        subprocess.run(["defaults", "write", DOMAIN, "keel.lang", "zh"], check=True)

        home_state = call("/home")
        check(home_state["has_data"] is False, "starts with no data")
        a = call("/assumptions")["current"]
        check(a["expected_return"]["brokerage"] == 0.08, "planning defaults complete without files")
        snapshot(out / "shots" / "1-first-run", "now,guide,plan,settings", {})

        print("2. First statement: Chase checking, three months in one file")
        r = upload(export("Chase0001_Activity_20261108.CSV",
                          [f"Chase0001_Activity_2026{m}.CSV" for m in ("10", "09", "08")], downloads))[0]
        check(r["status"] == "needs_account" and r["suggested_account"] == "Assets:Chase:Checking-0001",
              "recognised, asks which account")
        snapshot(out / "shots" / "2-pending", "pending", {})
        c = claim(r)
        check(c["status"] == "imported" and c["new"] > 0, f"imported {c.get('new')} lines")
        steps = {s["id"]: s["done"] for s in call("/home")["first_run"]}
        check(steps["import"] and steps["balance"], "checking balance came with the statement")

        print("3. A card whose export has no balance: Amex")
        r = upload(export("activity.csv", [f"amex_activity_2026{m}.csv" for m in ("08", "09", "10")],
                          downloads))[0]
        check(r["importer"] == "amex", "generic filename still recognised as Amex")
        claim(r)
        todos = call("/home")["todos"]
        check(any(t["kind"] == "balance" and "American Express" in t["title"] for t in todos),
              "asks to check the Amex balance")
        groups = {g["key"]: g for g in call("/review/groups")}
        check("信用卡" in (groups.get("chase credit crd", {}).get("hint") or ""), "card payment hint")
        check("投资账户" in (groups.get("vanguard buy investment", {}).get("hint") or ""), "brokerage hint")
        check("Assets:Elsewhere" in call("/categories"), "我的其他账户 offered")

        print("4. Categorise like a newcomer")
        for key, cat in (("zelle payment to oakwood apartments", "Expenses:Housing:Rent"),
                         ("vanguard buy investment", "Assets:Elsewhere")):
            call("/review/groups/apply", {"txn_ids": groups[key]["txn_ids"], "category": cat, "remember": True})
        check(call("/review/summary")["uncategorized"] == 2, "only the card payments wait")

        print("5. The other card arrives; payments pair by themselves")
        r = upload(export("Chase0002_Activity_20261108.CSV",
                          [f"Chase0002_Activity_2026{m}.CSV" for m in ("10", "09", "08")], downloads))[0]
        claim(r)
        summary = call("/review/summary")
        check(summary["uncategorized"] == 0 and summary["possible_transfers"] == 0, "nothing left to review")
        check(call("/reconcile") == [], "balances reconcile")

        print("6. Check the two card balances")
        sheet = {l["account"]: l["balance"] for l in call("/reports/balance-sheet")["lines"]}
        for acct in ("Liabilities:American Express:Card", "Liabilities:Chase:Card-0002"):
            call("/accounts/balance", {"account": acct, "date": dt.date.today().isoformat(),
                                       "amount": sheet[acct]})
        state = call("/home")
        check(not [t for t in state["todos"] if t["kind"] == "balance"], "no balance left to check")
        check(all(s["done"] for s in state["first_run"]), "first-run checklist complete")

        print("7. Alipay: an encrypted export")
        shutil.copy(ROOT / "fixtures" / "cn" / "alipay_record_202609.zip", downloads)
        r = upload(downloads / "alipay_record_202609.zip")[0]
        check(r["status"] == "needs_password", "asks for the zip password")
        snapshot(out / "shots" / "7-password", "pending", {})
        u = call(f"/onboarding/pending/{r['pending_sha']}/unlock", {"password": "246810", "remember": False})
        check(u["status"] == "imported", f"unzipped and imported {u.get('new')} lines")
        check(not (home / "secrets.json").exists(), "password not saved when not asked to")
        flow = call("/reports/cashflow?start=2026-08&end=2026-10&depth=1")
        check(not flow.get("missing_fx"), "CNY converts before any rate is downloaded")

        print("8. Planning from the ledger")
        d = call("/sim/defaults")["scenario"]
        check(80_000 <= d["annual_income"] <= 120_000, f"income scaled to a year ({d['annual_income']:,.0f})")
        check(20_000 <= d["annual_spending"] <= 80_000, f"spending scaled to a year ({d['annual_spending']:,.0f})")

        snapshot(out / "shots" / "9-with-data", "now,report,year,calendar,assets,transactions,spending,plan", {})
        print("9. Screens rendered to", out / "shots")
    finally:
        engine.terminate()
        engine.wait(timeout=20)
        subprocess.run(["defaults", "delete", DOMAIN], capture_output=True)
        if backup.exists():
            subprocess.run(["defaults", "import", DOMAIN, str(backup)], capture_output=True)

    print(f"\n{len(failures)} failed" if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))
