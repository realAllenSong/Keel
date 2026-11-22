"""Local-only API hardening.

The API listens on loopback, but any web page open in the user's browser can still *send* requests
to 127.0.0.1:<port>. Two cheap checks close the classic holes:
  * Host must be a loopback name -> defeats DNS rebinding (attacker domain resolving to 127.0.0.1).
  * State-changing requests carrying an Origin must come from a loopback origin -> defeats
    cross-site form/fetch posts (CSRF). Requests without Origin (CLI, curl, same-origin GETs) pass.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]", "testserver"}  # testserver = TestClient
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _hostname(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("["):  # [::1]:8711
        return value[: value.find("]") + 1]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


class LocalOnlyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        host = _hostname(headers.get("host", ""))
        reason = None
        if host not in LOOPBACK:
            reason = "host not allowed"
        elif (
            scope["method"] not in SAFE_METHODS
            and (origin := headers.get("origin"))
            and (origin == "null" or (urlsplit(origin).hostname or "") not in LOOPBACK)
        ):
            reason = "cross-site request blocked"
        if reason is None:
            await self.app(scope, receive, send)
            return
        body = f'{{"detail":"{reason}"}}'.encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
