"""Language of server-written text (insights, to-dos, notifications, error messages).

Source strings are Chinese, as everywhere in the codebase; `tr()` returns the English version
from pf/i18n_en.py when the request asked for English (Accept-Language, set by the app
from Settings) or, outside a request (daily job, notifications), when the saved preference
resolves to English. Missing entries fall back to Chinese; tests/test_i18n.py fails on them.
"""

from __future__ import annotations

import os
import platform
import subprocess
from collections.abc import Awaitable, Callable, MutableMapping
from contextvars import ContextVar
from typing import Any, Literal

from pf.i18n_en import EN

Lang = Literal["zh", "en"]
LANG: ContextVar[Lang] = ContextVar("lang", default="zh")


def tr(zh: str, **values: object) -> str:
    s = zh if LANG.get() == "zh" else EN.get(zh, zh)
    return s.format(**values) if values else s


def parse_accept_language(header: str | None) -> Lang:
    first = (header or "").split(",")[0].strip().lower()
    return "en" if first.startswith("en") else "zh"


def system_lang() -> Lang:
    """The OS language (macOS: first preferred language; elsewhere: $LANG)."""
    if platform.system() == "Darwin":
        try:
            out = subprocess.run(
                ["defaults", "read", "-g", "AppleLanguages"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout
            for token in out.replace("(", " ").replace(")", " ").replace(",", " ").split():
                code = token.strip('"').lower()
                if code:
                    return "zh" if code.startswith("zh") else "en"
        except (OSError, subprocess.SubprocessError):
            pass
    return "zh" if os.environ.get("LANG", "").lower().startswith("zh") else "en"


def resolve(pref: str) -> Lang:
    return "zh" if pref == "zh" else "en" if pref == "en" else system_lang()


ASGIApp = Callable[
    [MutableMapping[str, Any], Callable[[], Awaitable[Any]], Callable[[Any], Awaitable[None]]],
    Awaitable[None],
]


class LanguageMiddleware:
    """Sets LANG for the request from its Accept-Language header (pure ASGI: the value is visible
    in sync endpoints running in the thread pool too)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header = next(
            (v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"accept-language"),
            None,
        )
        token = LANG.set(parse_accept_language(header))
        try:
            await self.app(scope, receive, send)
        finally:
            LANG.reset(token)
