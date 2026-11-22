"""Build Keel.app: the SwiftUI app with the Python engine inside, signed (optionally notarized).

    make app          # from the repo root; writes dist/Keel.app (KEEL_DIST overrides dist/)

Steps: engine (PyInstaller, desktop/KeelEngine.spec) -> `swift build -c release` -> assemble the
bundle -> sign everything inside out with the Developer ID found in the keychain (ad-hoc without
one; SIGN_IDENTITY overrides) -> NOTARIZE=1 notarizes and staples (NOTARY_PROFILE, default
Keel-notary). Runs in the desktop/ uv environment, which provides PyInstaller.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPLE = ROOT / "apple"
DESKTOP = ROOT / "desktop"
DIST = Path(os.environ.get("KEEL_DIST") or ROOT / "dist")
APP = DIST / "Keel.app"
ENGINE_BUILD = DESKTOP / "build" / "engine"
BUILD_INFO = DESKTOP / "build" / "build-info.json"
ENTITLEMENTS = DESKTOP / "entitlements.plist"
APP_ID = "com.allensong.keel"
VERSION = "1.0.0"
CHANNEL = os.environ.get("KEEL_CHANNEL", "source")  # release: what people download
MACHO = {
    b"\xcf\xfa\xed\xfe",
    b"\xce\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xfe\xed\xfa\xcf",
}


def run(args: list[str | Path], **kw) -> subprocess.CompletedProcess:  # type: ignore[no-untyped-def]
    print("$", " ".join(map(str, args)), flush=True)
    return subprocess.run([str(a) for a in args], check=True, **kw)


def git(*args: str) -> str | None:
    out = subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False
    )
    return (out.stdout.strip() or None) if out.returncode == 0 else None


# ---------------------------------------------------------------- build


def build_engine() -> Path:
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            DESKTOP / "KeelEngine.spec",
            "--noconfirm",
            "--clean",
            "--distpath",
            ENGINE_BUILD / "dist",
            "--workpath",
            ENGINE_BUILD / "work",
        ],
        cwd=ROOT,
        env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(ENGINE_BUILD / "cache")},
    )
    return ENGINE_BUILD / "dist" / "keel-engine"


def build_swift() -> Path:
    run(["swift", "build", "-c", "release", "--product", "Keel"], cwd=APPLE)
    out = run(
        ["swift", "build", "-c", "release", "--show-bin-path"],
        cwd=APPLE,
        capture_output=True,
        text=True,
    )
    return Path(out.stdout.strip()) / "Keel"


def write_build_info() -> None:
    """What this build is. A source build names its clone and branch (updates pull from it); a
    release build carries only its version and channel (updates come from GitHub Releases), so
    no path from the build machine ships inside the public app."""
    info: dict[str, object] = {
        "version": VERSION,
        "channel": CHANNEL,
        "agent_cli": 1,  # the engine answers `keel-engine agent …` (the `keel` command and MCP)
        "commit": git("rev-parse", "HEAD"),
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if CHANNEL != "release":
        info |= {
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "repo": str(ROOT),
            "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        }
    BUILD_INFO.parent.mkdir(parents=True, exist_ok=True)
    BUILD_INFO.write_text(json.dumps(info, indent=2))


def assemble(binary: Path, engine: Path) -> None:
    if APP.exists():
        shutil.rmtree(APP)
    contents = APP / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    resources = contents / "Resources"
    resources.mkdir()
    shutil.copy2(binary, contents / "MacOS" / "Keel")
    shutil.copy2(DESKTOP / "assets" / "Keel.icns", resources / "Keel.icns")
    # Geist / Geist Mono (SIL OFL, licence alongside), registered for the app by ATSApplicationFontsPath.
    shutil.copytree(APPLE / "Fonts", resources / "Fonts")
    for asset in (APPLE / "Assets").glob("*.png"):  # the in-app mark (KeelMark)
        shutil.copy2(asset, resources / asset.name)
    for legal in ("LICENSE", "NOTICE"):  # AGPL-3.0 and its attribution terms travel with the app
        shutil.copy2(ROOT / legal, resources / legal)
    shutil.copytree(engine, resources / "engine" / "keel-engine", symlinks=True)
    write_build_info()
    shutil.copy2(BUILD_INFO, resources / "build-info.json")
    info = {
        "CFBundleIdentifier": APP_ID,
        "CFBundleName": "Keel",
        "CFBundleDisplayName": "Keel",
        "CFBundleExecutable": "Keel",
        "CFBundleIconFile": "Keel",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": git("rev-list", "--count", "HEAD") or "1",
        "CFBundleDevelopmentRegion": "en",
        "CFBundleLocalizations": ["en", "zh-Hans"],
        "LSMinimumSystemVersion": "14.0",
        "LSApplicationCategoryType": "public.app-category.finance",
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "ATSApplicationFontsPath": "Fonts",
        "NSDownloadsFolderUsageDescription": (
            "Keel looks for bank statements you download and imports them into your local ledger. "
            "It never moves or deletes your downloads."
        ),
    }
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump(info, f)
    (contents / "PkgInfo").write_text("APPL????")


# ---------------------------------------------------------------- sign / notarize


def signing_identity() -> str:
    if override := os.environ.get("SIGN_IDENTITY"):
        return override
    result = run(
        ["security", "find-identity", "-v", "-p", "codesigning"],
        capture_output=True,
        text=True,
    )
    matches = sorted(re.findall(r'"(Developer ID Application:[^"]+)"', result.stdout))
    return matches[0] if matches else "-"


def nested_code() -> tuple[list[Path], list[Path]]:
    files, bundles = [], []
    main = APP / "Contents/MacOS/Keel"
    for path in (APP / "Contents").rglob("*"):
        if path.is_symlink():
            continue
        if path.is_file() and path != main:
            with path.open("rb") as stream:
                if stream.read(4) in MACHO:
                    files.append(path)
        elif path.is_dir() and path.suffix in (
            ".app",
            ".framework",
            ".bundle",
            ".appex",
            ".xpc",
        ):
            bundles.append(path)
    return files, sorted(bundles, key=lambda p: -len(p.parts))


def sign_app() -> str:
    """Inside out: every Mach-O in the engine, nested bundles, then the app (hardened runtime)."""
    identity = signing_identity()
    print(f"Signing {APP} with {identity}", flush=True)
    if identity == "-":
        run(
            [
                "codesign",
                "--force",
                "--deep",
                "--sign",
                "-",
                "--entitlements",
                ENTITLEMENTS,
                APP,
            ]
        )
    else:
        base = [
            "codesign",
            "--force",
            "--sign",
            identity,
            "--options",
            "runtime",
            "--timestamp",
            "--entitlements",
            ENTITLEMENTS,
        ]
        files, bundles = nested_code()

        def sign_file(path: Path) -> None:
            # Apple's timestamp service drops a request now and then: try again before failing.
            for attempt in range(4):
                result = subprocess.run(
                    [str(p) for p in (*base, path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if not result.returncode:
                    return
                if "timestamp service is not available" not in result.stderr:
                    break
                time.sleep(2 * (attempt + 1))
            raise RuntimeError(f"codesign {path}: {result.stderr}")

        with ThreadPoolExecutor(8) as pool:
            list(pool.map(sign_file, files))
        for bundle in bundles:
            run([*base, bundle])
        run([*base, "--identifier", APP_ID, APP])
    run(["codesign", "--verify", "--deep", "--strict", APP])
    return identity


def notarize() -> None:
    archive = DIST / "Keel-notarize.zip"
    run(["ditto", "-c", "-k", "--keepParent", APP, archive])
    try:
        profile = os.environ.get("NOTARY_PROFILE", "Keel-notary")
        run(
            [
                "xcrun",
                "notarytool",
                "submit",
                archive,
                "--keychain-profile",
                profile,
                "--wait",
            ]
        )
    finally:
        archive.unlink(missing_ok=True)
    run(["xcrun", "stapler", "staple", APP])
    run(["xcrun", "stapler", "validate", APP])
    run(["spctl", "--assess", "--type", "execute", "-vv", APP])


def package_release(identity: str) -> None:
    """What a release publishes: Keel.dmg for the website (drag to Applications; signed and
    notarized itself) and Keel-macOS.zip for in-app updates (ditto keeps the stapled app intact)."""
    import hashlib
    import tempfile

    profile = os.environ.get("NOTARY_PROFILE", "Keel-notary")
    archive = DIST / "Keel-macOS.zip"
    archive.unlink(missing_ok=True)
    run(["ditto", "-c", "-k", "--keepParent", APP, archive])
    dmg = DIST / "Keel.dmg"
    dmg.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="keel-dmg-") as tmp:
        stage = Path(tmp)
        run(["ditto", APP, stage / "Keel.app"])
        (stage / "Applications").symlink_to("/Applications")  # drag here to install
        run(["hdiutil", "create", "-volname", "Keel", "-srcfolder", stage, "-ov", "-format", "UDZO", dmg])
    run(["codesign", "--force", "--sign", identity, "--timestamp", dmg])
    run(["xcrun", "notarytool", "submit", dmg, "--keychain-profile", profile, "--wait"])
    run(["xcrun", "stapler", "staple", dmg])
    run(["spctl", "--assess", "--type", "open", "--context", "context:primary-signature", "-vv", dmg])
    for f in (dmg, archive):
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        Path(f"{f}.sha256").write_text(f"{digest}  {f.name}\n")
        print(f"{f.name}  {f.stat().st_size / 1e6:.1f} MB  sha256 {digest}")


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("Keel.app builds on macOS (Xcode command line tools + uv).")
    engine = build_engine()
    binary = build_swift()
    DIST.mkdir(parents=True, exist_ok=True)
    assemble(binary, engine)
    identity = sign_app()
    if os.environ.get("NOTARIZE") == "1":
        if not identity.startswith("Developer ID Application:"):
            raise SystemExit(
                "Notarization needs a Developer ID Application certificate"
            )
        notarize()
    if CHANNEL == "release":
        if os.environ.get("NOTARIZE") != "1":
            raise SystemExit("A release must be notarized: run `make release`.")
        package_release(identity)
    print(json.dumps({"app": str(APP), "signed_with": identity, "version": VERSION, "channel": CHANNEL}))


if __name__ == "__main__":
    main()
