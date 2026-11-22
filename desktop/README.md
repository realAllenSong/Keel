# Keel's engine

The SwiftUI app (`apple/`) does all the drawing. Everything else (ledger, importers, reports,
simulations, the inbox watcher and the daily / monthly jobs) runs in the **engine**: the
`pf` backend frozen with PyInstaller (`KeelEngine.spec`, entry point `ledger_app/engine.py`)
and shipped inside the app at `Keel.app/Contents/Resources/engine/keel-engine/`.

The app starts the engine with a pipe on stdin. The engine migrates the database, serves the API
on a random `127.0.0.1` port, prints `KEEL_READY <url>`, starts the watcher and the jobs, and
exits when the pipe closes, so it never outlives the app.

## Build and sign

`make app` (see `apple/build.py`) freezes the engine, compiles the Swift app, assembles
`dist/Keel.app` and signs it inside out: every Mach-O in the engine, then the app, with hardened
runtime and `entitlements.plist`. It picks the first Developer ID Application certificate in the
keychain, or signs ad hoc without one (`SIGN_IDENTITY=-` forces ad hoc).
`NOTARIZE=1 NOTARY_PROFILE=Keel-notary make app` also notarizes and staples (create the keychain
profile once with `xcrun notarytool store-credentials`). `generate_icon.py` draws the icon.

## Updating

Builds record the commit and branch they were made from (`build-info.json` in the bundle).
设置 → 版本与更新 (or the menu bar's 检查更新…) runs `git fetch` in that clone with your own git
credentials, so a private repository needs no token, and lists the new commits. 更新并重启:

1. backs up the ledger;
2. `git pull --ff-only` the same branch;
3. `KEEL_DIST=dist/update-staging make app`, so the running app is untouched while it builds (a
   failure stops here and is shown in Settings, with the log in `~/Library/Logs/Keel/update.log`);
4. quits Keel, moves the old bundle to `dist/update-staging/Keel.previous.app` (rollback: move it
   back) and puts the new one where the old one was;
5. reopens Keel. Database migrations run on start, as always.

The clone must stay where it was built and have no uncommitted changes. Manual update or first
install: `git pull && make install`.

## Local state

App home: `~/Library/Application Support/Keel`, with `data/ledger.db`, `raw/`, `config/`,
`Inbox/` and `backups/` (`PF_APP_HOME` moves it; every backend `PF_*` override still applies).
Examples are copied on first run. Logs rotate in `~/Library/Logs/Keel/` (`PF_LOG_DIR`).

Downloads are scanned read-only; the app-owned Inbox archives processed files. Daily market
updates and verified backups run once after 07:30 local while Keel is running; on the 1st the
previous month's snapshot is saved. Attempt markers in `data/desktop-jobs.json` prevent duplicate
runs across restarts. 开机自动启动 registers the app with `SMAppService` from Swift.

## Tests

```sh
cd desktop && uv run --python 3.12 pytest -q tests    # runtime, jobs, icon
```
