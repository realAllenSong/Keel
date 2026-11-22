#!/usr/bin/env python3
"""Every L("…") string in Sources has English in L10nEN.swift, placeholders match, and no Chinese
appears outside L() (lines marked `l10n-allow` and the zh name table in Format.swift excepted).
Usage: check_l10n.py [--missing]   (exit 1 on problems)"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "Sources"
EN_FILE = SRC / "KeelCore" / "L10nEN.swift"
HAN = re.compile(r"[一-鿿]")
CALL = re.compile(r'\bL\(\s*"((?:[^"\\]|\\.)*)"')
ENTRY = re.compile(r'^\s*"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)",?\s*$', re.M)


def unescape(s: str) -> str:
    return s.encode().decode("unicode_escape").encode("latin-1").decode("utf-8")


def main() -> int:
    entries = ENTRY.findall(EN_FILE.read_text()) if EN_FILE.exists() else []
    duplicates = [k for k, count in Counter(unescape(k) for k, _ in entries).items() if count > 1]
    en = {unescape(k): unescape(v) for k, v in entries}
    used: dict[str, str] = {}
    stray: list[str] = []
    for f in sorted(SRC.rglob("*.swift")):
        if f == EN_FILE:
            continue
        text = f.read_text()
        for m in CALL.finditer(text):
            used.setdefault(unescape(m.group(1)), str(f.relative_to(SRC)))
        in_table = False
        for i, line in enumerate(text.splitlines(), 1):
            if "zhSegments" in line and "[String: String] = [" in line:
                in_table = True
            if in_table:
                if line.strip() == "]":
                    in_table = False
                continue
            code = re.sub(r"//.*$", "", line)
            code = CALL.sub("", code)
            if HAN.search(code) and "l10n-allow" not in line:
                stray.append(f"{f.relative_to(SRC)}:{i}: {line.strip()[:100]}")
    missing = {s: f for s, f in used.items() if HAN.search(s) and s not in en}
    if "--missing" in sys.argv:
        for s, f in missing.items():
            print(f"{s}\t{f}")
        return 0
    names = re.compile(r"\{(\w+)\}")
    bad = [k for k, v in en.items() if sorted(names.findall(k)) != sorted(names.findall(v))]
    unused = [k for k in en if k not in used]
    for title, items in (("duplicate keys", duplicates), ("missing English", missing), ("placeholder mismatch", bad), ("unused", unused), ("Chinese outside L()", stray)):
        if items:
            print(f"{len(items)} {title}:", *items, sep="\n  ", file=sys.stderr)
    return 1 if (duplicates or missing or bad or unused or stray) else 0


if __name__ == "__main__":
    sys.exit(main())
