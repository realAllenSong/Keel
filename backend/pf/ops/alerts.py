"""Send each new pulse alert (overspending pace, a paycheck that did not arrive, cash running low,
an annual renewal) as one macOS notification, once. Run by the daily job and after the inbox
imports new statements."""

from __future__ import annotations

import json

from sqlalchemy import Connection, text

from pf.i18n import LANG, resolve
from pf.ops.notify import notify
from pf.prefs import load_prefs
from pf.reports.pulse import pulse

KEEP = 300  # ids remembered; older ones can't come back (they carry their date)


def notify_new_alerts(
    conn: Connection, *, severities: tuple[str, ...] = ("warning", "notice")
) -> list[str]:
    prefs = load_prefs(conn)
    if not prefs.alerts:
        return []
    token = LANG.set(resolve(prefs.language))
    try:
        p = pulse(conn)
    finally:
        LANG.reset(token)
    if p is None:
        return []
    raw = conn.execute(text("SELECT value FROM app_settings WHERE key='alerts_seen'")).scalar()
    seen: list[str] = json.loads(raw) if raw else []
    sent = []
    for a in p.alerts:
        if a.severity in severities and a.id not in seen:
            notify(a.title, a.body)
            seen.append(a.id)
            sent.append(a.id)
    if sent:
        conn.execute(
            text("""INSERT INTO app_settings(key, value) VALUES ('alerts_seen', :v)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value"""),
            {"v": json.dumps(seen[-KEEP:])},
        )
    return sent
