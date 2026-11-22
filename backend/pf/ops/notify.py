"""macOS notifications (no-op elsewhere or when PF_NOTIFY=0)."""

from __future__ import annotations

import os
import platform
import subprocess


def notify(title: str, message: str) -> None:
    if os.environ.get("PF_NOTIFY", "1") == "0" or platform.system() != "Darwin":
        return
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
    script = f'display notification "{esc(message)}" with title "Keel · {esc(title)}"'
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True, timeout=10)
