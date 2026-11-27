"""Find the importer that understands a file."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import Connection, text

from pf.importers.base import Importer, ImportError_, read_head
from pf.importers.brokers import (
    FidelityImporter,
    IbkrHistoryImporter,
    IbkrImporter,
    SchwabImporter,
)
from pf.importers.cn_banks import (
    BankOfChinaImporter,
    ChinaMerchantsBankImporter,
    NingboBankImporter,
)
from pf.importers.cn_wallets import AlipayImporter, WeChatImporter
from pf.importers.connector_json import ConnectorImporter
from pf.importers.generic_csv import GenericCsvImporter, Profile, load_profiles
from pf.importers.keel_csv import KeelCsvImporter
from pf.importers.okx import OkxImporter
from pf.settings import get_settings


def all_importers(conn: Connection | None = None) -> list[Importer]:
    s = get_settings()
    importers: list[Importer] = [
        ConnectorImporter(),
        KeelCsvImporter(),
        AlipayImporter(),
        WeChatImporter(),
        SchwabImporter(),
        FidelityImporter(),
        IbkrImporter(),
        IbkrHistoryImporter(),
        ChinaMerchantsBankImporter(),
        BankOfChinaImporter(),
        NingboBankImporter(),
        OkxImporter(),
    ]
    importers += [GenericCsvImporter(p) for p in load_profiles(s.config_dir / "importers")]
    if conn is not None:
        importers.extend(
            GenericCsvImporter(Profile(json.loads(r.body)))
            for r in conn.execute(text("SELECT body FROM user_profiles ORDER BY id"))
        )
    return importers


def get_importer(name: str, conn: Connection | None = None) -> Importer:
    for imp in all_importers(conn):
        if imp.name == name:
            return imp
    raise ImportError_(f"unknown importer {name!r}")


def detect(path: Path, conn: Connection | None = None) -> Importer:
    head = read_head(path)
    hits = [imp for imp in all_importers(conn) if imp.sniff(path, head)]
    if not hits:
        raise ImportError_(f"{path.name}: no importer recognises this file")
    # Prefer the most specific generic profile (more required header columns).
    hits.sort(key=_specificity, reverse=True)
    if len(hits) > 1 and _specificity(hits[0]) == _specificity(hits[1]):
        names = ", ".join(h.name for h in hits)
        raise ImportError_(f"{path.name}: ambiguous format ({names}); pass --importer")
    return hits[0]


def _specificity(imp: Importer) -> int:
    if isinstance(imp, GenericCsvImporter):
        d = imp.profile.raw.get("detect", {})
        return len(d.get("header", [])) + (5 if d.get("filename") else 0)
    return 100  # dedicated parsers win over generic profiles
