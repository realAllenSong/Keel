#!/usr/bin/env bash
# Build Keel.app from this checkout and replace /Applications/Keel.app with it.
# Your data stays in ~/Library/Application Support/Keel; it is backed up first.
# Usage: make install        (or INSTALL_DIR=~/Applications make install)
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${INSTALL_DIR:-/Applications}/Keel.app"
OLD="dist/Keel.previous.app"

echo "→ Backing up your ledger"
(cd backend && PF_DB="$HOME/Library/Application Support/Keel/data/ledger.db" \
  PF_DATA_DIR="$HOME/Library/Application Support/Keel/data" \
  PF_RAW_DIR="$HOME/Library/Application Support/Keel/raw" \
  PF_BACKUP_DIR="$HOME/Library/Application Support/Keel/backups" \
  uv run pf backup run) || echo "  (no existing ledger to back up)"

echo "→ Building Keel.app (a few minutes)"
make app

echo "→ Replacing $DEST"
osascript -e 'tell application id "com.allensong.keel" to quit' 2>/dev/null || true
for _ in $(seq 1 30); do pgrep -f "Keel.app/Contents/MacOS/Keel" >/dev/null || break; sleep 1; done
pkill -f "Keel.app/Contents/MacOS/Keel" 2>/dev/null || true
rm -rf "$OLD"
[ -d "$DEST" ] && mv "$DEST" "$OLD"
mv dist/Keel.app "$DEST"
echo "  previous version kept at $OLD (to roll back: move it back to $DEST)"

open "$DEST"
scripts/install-agent.sh  # the `keel` command for agents
echo "✓ Keel $(git rev-parse --short HEAD) installed. Next time: git pull && make install, or 设置 → 版本与更新."
