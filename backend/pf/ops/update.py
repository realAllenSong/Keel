"""Update Keel.app: from GitHub Releases (the public app) or from the git clone it was built from.

Release channel (builds made with KEEL_CHANNEL=release, i.e. what people download):
  check   ask GitHub for the latest release of realAllenSong/Keel and compare its version tag.
  apply   back up the ledger, download Keel-macOS.zip, unpack it with ditto (keeps signatures),
          refuse it unless it is signed by Keel's team and notarized, then quit, swap (the old
          app is kept for a rollback) and relaunch.

Source channel (a developer's own `make app`):

The repository is private and Keel is built on this Mac (`make app`), so the simplest update
that needs no release server or token is the one a developer would do by hand, behind a button:

  check   `git fetch` the branch the app was built from (your own git credentials) and list the
          commits since the build.
  apply   back up the ledger, then a detached script runs: `git pull --ff-only`, build into a
          staging folder, quit Keel, swap the new Keel.app into place (the old one is kept for a
          rollback), relaunch. If the pull or build fails, the running app is never touched.

The desktop shell tells the backend where it lives (KEEL_APP_BUNDLE, KEEL_APP_PID) and what it
was built from (KEEL_BUILD_INFO, written by apple/build.py). Without them (`make dev`), the
check still works against the source checkout and apply explains how to restart instead.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pf.settings import get_settings

APP_ID = "com.allensong.keel"
RELEASES_API = os.environ.get(
    "KEEL_RELEASES_API", "https://api.github.com/repos/realAllenSong/Keel/releases/latest"
)
RELEASE_ASSET = "Keel-macOS.zip"
TEAM_ID = (
    "VA85NH6QKH"  # Developer ID team that signs Keel; updates signed by anyone else are refused
)
SOURCE_ROOT = Path(__file__).resolve().parents[3]  # backend/pf/ops/update.py -> repo


@dataclass
class BuildInfo:
    commit: str | None
    branch: str | None
    repo: str | None
    built_at: str | None
    bundle: str | None  # the running Keel.app, if any
    source_mode: bool
    version: str | None = None
    channel: str = "source"  # release: update from GitHub Releases; source: from the git clone


@dataclass
class Change:
    commit: str
    subject: str
    date: str


@dataclass
class UpdateCheck:
    current: str | None
    latest: str | None
    behind: int = 0
    changes: list[Change] = field(default_factory=list)
    can_apply: bool = False
    reason: str | None = None  # why it can't be applied (shown to you)
    error: str | None = None
    checked_at: str = ""


def _git(repo: str | Path, *args: str, timeout: int = 60) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # never hang on a password prompt
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    if out.returncode:
        lines = (out.stderr or out.stdout).strip().splitlines()
        raise RuntimeError(lines[-1] if lines else f"git {args[0]} failed")
    return out.stdout.strip()


def build_info() -> BuildInfo:
    bundle = os.environ.get("KEEL_APP_BUNDLE") or None
    path = os.environ.get("KEEL_BUILD_INFO")
    if path and Path(path).is_file():
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return BuildInfo(
            commit=raw.get("commit"),
            branch=raw.get("branch"),
            repo=raw.get("repo"),
            built_at=raw.get("built_at"),
            bundle=bundle,
            source_mode=False,
            version=raw.get("version"),
            channel=raw.get("channel") or "source",
        )
    repo = SOURCE_ROOT if (SOURCE_ROOT / ".git").exists() else None
    commit = branch = None
    if repo:
        try:
            commit = _git(repo, "rev-parse", "HEAD")
            branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        except (RuntimeError, OSError, subprocess.SubprocessError):
            pass
    return BuildInfo(commit, branch, str(repo) if repo else None, None, bundle, bundle is None)


def _status_path() -> Path:
    get_settings().data_dir.mkdir(parents=True, exist_ok=True)
    return get_settings().data_dir / "update-status.json"


def _log_path() -> Path:
    logs = Path(os.environ.get("PF_LOG_DIR", get_settings().data_dir / "logs"))
    logs.mkdir(parents=True, exist_ok=True)
    return logs / "update.log"


def status() -> dict[str, str]:
    """Last update attempt: state = running | failed | done (written by the update script)."""
    try:
        raw = json.loads(_status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if raw.get("state") == "failed":
        with contextlib.suppress(OSError):
            raw["log"] = "\n".join(_log_path().read_text(errors="replace").splitlines()[-25:])
    return raw


def _version(v: str | None) -> tuple[int, ...]:
    parts = []
    for p in (v or "").lstrip("vV").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _latest_release() -> dict:
    import urllib.request

    req = urllib.request.Request(
        RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "Keel-updater"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _check_release(info: BuildInfo) -> UpdateCheck:
    now = dt.datetime.now().isoformat(timespec="seconds")
    result = UpdateCheck(current=info.version, latest=None, checked_at=now)
    try:
        rel = _latest_release()
    except Exception as exc:  # offline, rate-limited, GitHub down: say so, don't crash
        result.error = str(exc)
        return result
    tag = str(rel.get("tag_name") or "")
    result.latest = tag.lstrip("vV") or None
    asset = next((a for a in rel.get("assets") or [] if a.get("name") == RELEASE_ASSET), None)
    newer = _version(result.latest) > _version(info.version)
    result.behind = 1 if newer else 0
    published = str(rel.get("published_at") or "")[:10]
    notes = [line.strip("-* ").strip() for line in str(rel.get("body") or "").splitlines()]
    result.changes = [Change(tag, n, published) for n in notes if n][:30]
    if not newer:
        result.reason = "up_to_date"
    elif not asset:
        result.reason = "no_asset"
    elif not info.bundle:
        result.reason = "source_mode"
    else:
        result.can_apply = True
    return result


def check(fetch: bool = True) -> UpdateCheck:
    info = build_info()
    if info.channel == "release":
        return _check_release(info)
    now = dt.datetime.now().isoformat(timespec="seconds")
    result = UpdateCheck(current=info.commit, latest=None, checked_at=now)
    if not info.repo or not Path(info.repo, ".git").exists():
        result.reason = "no_repo"
        return result
    if not info.branch or info.branch == "HEAD":
        result.reason = "detached"
        return result
    try:
        if fetch:
            _git(info.repo, "fetch", "--quiet", "origin", info.branch, timeout=120)
        upstream = f"origin/{info.branch}"
        result.latest = _git(info.repo, "rev-parse", upstream)
        base = info.commit or _git(info.repo, "rev-parse", "HEAD")
        result.behind = int(_git(info.repo, "rev-list", "--count", f"{base}..{upstream}"))
        log = _git(info.repo, "log", "--format=%h%x09%s%x09%cs", "-n", "30", f"{base}..{upstream}")
        result.changes = [Change(*line.split("\t", 2)) for line in log.splitlines() if line]
        dirty = _git(info.repo, "status", "--porcelain", "--untracked-files=no")
        head = _git(info.repo, "rev-parse", "HEAD")
        fast_forward = (
            subprocess.run(
                ["git", "-C", info.repo, "merge-base", "--is-ancestor", head, upstream],
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
    except (RuntimeError, OSError, subprocess.SubprocessError, ValueError) as exc:
        result.error = str(exc)
        return result
    if result.behind == 0:
        result.reason = "up_to_date"
    elif dirty:
        result.reason = "local_changes"
    elif not fast_forward:
        result.reason = "diverged"
    elif not info.bundle:
        result.reason = "source_mode"
    else:
        result.can_apply = True
    return result


# The script runs outside Keel (it quits and replaces it). Paths come in as environment.
SCRIPT = r"""
set -u
say() { printf '{"state":"%s","step":"%s","at":"%s","target":"%s"}\n' "$1" "$2" "$(date +%Y-%m-%dT%H:%M:%S)" "$TARGET" > "$STATUS"; echo "== $1 $2 $(date)"; }
fail() { say failed "$1"; exit 1; }
say running pull
git -C "$REPO" pull --ff-only origin "$BRANCH" || fail pull
TARGET=$(git -C "$REPO" rev-parse --short HEAD)
say running build
rm -rf "$STAGE"; mkdir -p "$STAGE"
(cd "$REPO" && eval "$BUILD_CMD") || fail build
[ -d "$STAGE/Keel.app" ] || fail build
say running restart
eval "$QUIT_CMD"
for _ in $(seq 1 120); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
kill -0 "$PID" 2>/dev/null && { kill "$PID"; sleep 2; }
rm -rf "$STAGE/Keel.previous.app"
mv "$APP" "$STAGE/Keel.previous.app" || fail swap
if ! mv "$STAGE/Keel.app" "$APP"; then mv "$STAGE/Keel.previous.app" "$APP"; fail swap; fi
say done restart
# Opening in the same instant the old copy quits can be swallowed (macOS still sees it running):
# give it a moment, then make sure Keel really came back.
sleep 1
for _ in 1 2 3 4 5; do
  eval "$OPEN_CMD"
  [ -n "${OPEN_CHECK:-}" ] || break
  sleep 2
  pgrep -f "$APP/Contents/MacOS/" >/dev/null && break
done
"""


def apply(*, wait: bool = False) -> dict[str, str]:
    """Start the update. Returns the initial status; poll status() afterwards."""
    info = build_info()
    checked = check(fetch=True)
    if not checked.can_apply:
        raise ValueError(checked.reason or checked.error or "cannot update")
    if info.channel == "release":
        return _apply_release(info, checked, wait=wait)
    assert info.repo and info.branch and info.bundle
    from pf.ops.backup import run_backup

    run_backup()  # migrations in the new version run on a ledger that has a fresh copy
    stage = Path(info.repo) / "dist" / "update-staging"
    env = {
        **os.environ,
        "REPO": info.repo,
        "BRANCH": info.branch,
        "APP": info.bundle,
        "PID": os.environ.get("KEEL_APP_PID", str(os.getpid())),
        "STAGE": str(stage),
        "STATUS": str(_status_path()),
        "TARGET": (checked.latest or "")[:7],
        "BUILD_CMD": os.environ.get(
            "KEEL_UPDATE_BUILD_CMD",
            # uv syncs Python dependencies by itself; SwiftPM resolves Swift ones.
            'KEEL_DIST="$STAGE" make app',
        ),
        "QUIT_CMD": os.environ.get(
            "KEEL_UPDATE_QUIT_CMD", f"osascript -e 'tell application id \"{APP_ID}\" to quit'"
        ),
        "OPEN_CMD": os.environ.get("KEEL_UPDATE_OPEN_CMD", 'open "$APP"'),
        # Confirm the relaunch (and retry) unless a test replaced the open command.
        "OPEN_CHECK": "" if os.environ.get("KEEL_UPDATE_OPEN_CMD") else "1",
    }
    script = Path(tempfile.mkdtemp(prefix="keel-update-")) / "update.sh"
    script.write_text(SCRIPT, encoding="utf-8")
    _status_path().write_text(
        json.dumps({"state": "running", "step": "start", "target": env["TARGET"]}),
        encoding="utf-8",
    )
    # A login shell finds uv and swift the way Terminal does (apps start with a bare PATH).
    shell = "/bin/zsh" if Path("/bin/zsh").exists() and sys.platform == "darwin" else "/bin/bash"
    cmd = [shell, "-lc", f"bash {shlex.quote(str(script))}"]
    with _log_path().open("a") as log:
        log.write(f"\n=== update {dt.datetime.now().isoformat(timespec='seconds')} ===\n")
        log.flush()
        proc = subprocess.Popen(
            cmd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
    if wait:
        proc.wait()
    return status()


# Release update: download, verify (Keel's team, notarized), quit, swap, relaunch.
RELEASE_SCRIPT = r"""
set -u
say() { printf '{"state":"%s","step":"%s","at":"%s","target":"%s"}\n' "$1" "$2" "$(date +%Y-%m-%dT%H:%M:%S)" "$TARGET" > "$STATUS"; echo "== $1 $2 $(date)"; }
fail() { say failed "$1"; exit 1; }
say running download
rm -rf "$STAGE"; mkdir -p "$STAGE"
curl -fL --retry 3 -o "$STAGE/update.zip" "$URL" || fail download
say running verify
/usr/bin/ditto -x -k "$STAGE/update.zip" "$STAGE/unpacked" || fail verify
NEW="$STAGE/unpacked/Keel.app"
[ -d "$NEW" ] || fail verify
codesign --verify --deep --strict "$NEW" || fail verify
codesign -dv "$NEW" 2>&1 | grep -q "TeamIdentifier=$TEAM" || fail verify
spctl --assess --type execute "$NEW" || fail verify
say running restart
eval "$QUIT_CMD"
for _ in $(seq 1 120); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
kill -0 "$PID" 2>/dev/null && { kill "$PID"; sleep 2; }
PREV="$STAGE/Keel.previous.app"  # kept with the download, out of /Applications and Spotlight
rm -rf "$PREV"
mv "$APP" "$PREV" || fail swap
if ! /usr/bin/ditto "$NEW" "$APP"; then rm -rf "$APP"; mv "$PREV" "$APP"; fail swap; fi
say done restart
# Opening in the same instant the old copy quits can be swallowed (macOS still sees it running):
# give it a moment, then make sure Keel really came back.
sleep 1
for _ in 1 2 3 4 5; do
  eval "$OPEN_CMD"
  [ -n "${OPEN_CHECK:-}" ] || break
  sleep 2
  pgrep -f "$APP/Contents/MacOS/" >/dev/null && break
done
"""


def _apply_release(info: BuildInfo, checked: UpdateCheck, *, wait: bool) -> dict[str, str]:
    assert info.bundle
    rel = _latest_release()
    asset = next(a for a in rel.get("assets") or [] if a.get("name") == RELEASE_ASSET)
    from pf.ops.backup import run_backup

    run_backup()  # migrations in the new version run on a ledger that has a fresh copy
    stage = Path(tempfile.mkdtemp(prefix="keel-release-"))
    env = {
        **os.environ,
        "URL": str(asset["browser_download_url"]),
        "APP": info.bundle,
        "TEAM": TEAM_ID,
        "PID": os.environ.get("KEEL_APP_PID", str(os.getpid())),
        "STAGE": str(stage),
        "STATUS": str(_status_path()),
        "TARGET": checked.latest or "",
        "QUIT_CMD": os.environ.get(
            "KEEL_UPDATE_QUIT_CMD", f"osascript -e 'tell application id \"{APP_ID}\" to quit'"
        ),
        "OPEN_CMD": os.environ.get("KEEL_UPDATE_OPEN_CMD", 'open "$APP"'),
        # Confirm the relaunch (and retry) unless a test replaced the open command.
        "OPEN_CHECK": "" if os.environ.get("KEEL_UPDATE_OPEN_CMD") else "1",
    }
    return _launch(RELEASE_SCRIPT, env, wait=wait)


def _launch(script_text: str, env: dict[str, str], *, wait: bool) -> dict[str, str]:
    script = Path(tempfile.mkdtemp(prefix="keel-update-")) / "update.sh"
    script.write_text(script_text, encoding="utf-8")
    _status_path().write_text(
        json.dumps({"state": "running", "step": "start", "target": env["TARGET"]}),
        encoding="utf-8",
    )
    with _log_path().open("a") as log:
        log.write(f"\n=== update {dt.datetime.now().isoformat(timespec='seconds')} ===\n")
        log.flush()
        proc = subprocess.Popen(
            ["/bin/bash", str(script)],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    if wait:
        proc.wait()
    return status()


def notify_available() -> str | None:
    """Daily job: one notification per new upstream commit. Returns the commit announced."""
    result = check()
    if result.behind <= 0 or not result.latest:
        return None
    seen = get_settings().data_dir / "update-notified"
    with contextlib.suppress(OSError):
        if seen.read_text().strip() == result.latest:
            return None
    from pf.i18n import tr
    from pf.ops.notify import notify

    notify(tr("Keel 有 {n} 项更新", n=result.behind), tr("在设置里点「更新并重启」即可安装。"))
    seen.write_text(result.latest)
    return result.latest
