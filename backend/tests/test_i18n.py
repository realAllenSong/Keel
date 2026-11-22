"""Every Chinese string handed to tr() has an English version, with the same placeholders, and
API text follows Accept-Language."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from fastapi.testclient import TestClient

from pf.i18n import LANG, parse_accept_language, tr
from pf.i18n_en import EN

PF = Path(__file__).resolve().parents[1] / "pf"
HAN = re.compile(r"[一-鿿]")


def _tr_literals() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in PF.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tr"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found[node.args[0].value] = f"{path.relative_to(PF.parent)}:{node.lineno}"
    return found


def test_every_tr_string_has_english() -> None:
    from pf.api.routers.home import _CHECK_LABELS, SHORT
    from pf.reports.lighthouses import TITLES
    from pf.sim.backtest import LABELS

    found = _tr_literals()
    # Constants translated where they are used: tr(LABELS[k]) and friends.
    checks = [s for pair in _CHECK_LABELS.values() for s in pair]
    for s in [*SHORT.values(), *TITLES, *LABELS.values(), *checks]:
        found[s] = "constant"
    missing = {s: where for s, where in found.items() if HAN.search(s) and s not in EN}
    assert not missing, "\n".join(f"{w}: {s!r}" for s, w in missing.items())


def test_placeholders_match() -> None:
    names = re.compile(r"\{(\w*)\}")
    bad = {k: v for k, v in EN.items() if sorted(names.findall(k)) != sorted(names.findall(v))}
    assert not bad


def test_tr_switches_language_and_fills_values() -> None:
    assert tr("{n} 处对账不符", n=2) == "2 处对账不符"
    token = LANG.set("en")
    try:
        assert tr("{n} 处对账不符", n=2) == "2 balances don't match statements"
        assert tr("没有翻译的句子") == "没有翻译的句子"  # falls back to Chinese
    finally:
        LANG.reset(token)
    assert parse_accept_language("en-US,en;q=0.9") == "en"
    assert parse_accept_language("zh-CN") == "zh" and parse_accept_language(None) == "zh"


def test_institution_guide_is_bilingual(env: Path) -> None:
    from pf.api.app import app

    with TestClient(app) as c:
        zh = c.get("/api/onboarding/institutions").json()
        en = c.get("/api/onboarding/institutions", headers={"Accept-Language": "en"}).json()
    assert [i["id"] for i in zh] == [i["id"] for i in en]
    assert zh[0]["steps"][0].startswith("在电脑上登录 chase.com")
    assert en[0]["steps"][0].startswith("Sign in at chase.com")
    # Steps may quote an app's own Chinese menu label (Alipay); names and notes are English.
    assert all(not HAN.search(i["note"] + i["name"]) for i in en)
