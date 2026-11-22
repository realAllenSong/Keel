#!/bin/sh
# Put `keel` on PATH (~/.local/bin/keel): Keel's CLI for you and for coding agents, and
# `keel mcp` for chat apps. It runs the engine inside the installed Keel.app; from a source
# checkout without an installed app it runs the same code with uv.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$HOME/.local/bin"
mkdir -p "$BIN"
cat > "$BIN/keel" <<SHIM
#!/bin/sh
# Keel for agents. Written by scripts/install-agent.sh; safe to re-run.
APP="/Applications/Keel.app/Contents/Resources"
if [ -x "\$APP/engine/keel-engine/keel-engine" ] && grep -q '"agent_cli"' "\$APP/build-info.json" 2>/dev/null; then
  exec "\$APP/engine/keel-engine/keel-engine" agent "\$@"
fi
if [ -d "$REPO/desktop" ] && command -v uv >/dev/null; then
  cd "$REPO/desktop" && exec uv run --quiet --python 3.12 python -m ledger_app.engine agent "\$@"
fi
echo '{"ok":false,"error":"not_installed","message":"Keel.app is not installed","fix":"install Keel from the website, then run keel doctor"}' >&2
exit 1
SHIM
chmod +x "$BIN/keel"
echo "keel -> $BIN/keel"
