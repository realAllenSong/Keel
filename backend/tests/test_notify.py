from __future__ import annotations

import subprocess

import pytest

from pf.ops.notify import notify


def test_notifications_identify_keel_and_escape_user_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PF_NOTIFY", "1")
    monkeypatch.setattr("pf.ops.notify.platform.system", lambda: "Darwin")
    scripts: list[str] = []

    def capture(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args[:2] == ["osascript", "-e"]
        scripts.append(args[2])
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("pf.ops.notify.subprocess.run", capture)
    notify('账单 "已导入"', "路径 \\ Inbox")

    assert scripts == [
        'display notification "路径 \\\\ Inbox" with title "Keel · 账单 \\"已导入\\""'
    ]
