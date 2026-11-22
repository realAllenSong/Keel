#!/usr/bin/env python3
"""Pre-commit privacy scan. Refuses to commit anything that looks like real personal data.

Detects:
  * payment card numbers (13-19 digits, Luhn-valid, separators allowed)
  * PRC resident ID numbers (18 chars, checksum-valid)
  * US SSNs (AAA-GG-SSSS)
  * any term listed in config/private_terms.local.txt (gitignored: your name, address, account
    suffixes, employer...), case-insensitive

A line can opt out with the marker `privacy-scan: allow` (use sparingly, e.g. for test vectors).
Usage: privacy_scan.py FILE...   (exit 1 on findings)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ALLOW_MARKER = "privacy-scan: allow"
ROOT = Path(__file__).resolve().parents[1]
TERMS_FILE = ROOT / "config" / "private_terms.local.txt"

CARD_RE = re.compile(r"(?<![\d.])(?:\d[ -]?){12,18}\d(?![\d.])")
CN_ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
SSN_RE = re.compile(r"(?<!\d)(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?!\d)")

CN_ID_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
CN_ID_CHECK = "10X98765432"


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def looks_like_card(raw: str) -> bool:
    digits = re.sub(r"[ -]", "", raw)
    if not 13 <= len(digits) <= 19 or len(set(digits)) <= 2:
        return False
    return digits[0] in "3456" and luhn_ok(digits)


def cn_id_ok(s: str) -> bool:
    s = s.upper()
    total = sum(int(c) * w for c, w in zip(s[:17], CN_ID_WEIGHTS, strict=True))
    return CN_ID_CHECK[total % 11] == s[17] and s[6:8] in ("19", "20")


def load_terms() -> list[str]:
    if not TERMS_FILE.exists():
        return []
    lines = TERMS_FILE.read_text(encoding="utf-8").splitlines()
    return [t.strip().lower() for t in lines if t.strip() and not t.startswith("#")]


def scan_text(text: str, terms: list[str]) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for m in CARD_RE.finditer(line):
            if looks_like_card(m.group()):
                findings.append((lineno, "possible card number"))
        for m in CN_ID_RE.finditer(line):
            if cn_id_ok(m.group()):
                findings.append((lineno, "possible PRC ID number"))
        if SSN_RE.search(line):
            findings.append((lineno, "possible SSN"))
        low = line.lower()
        for t in terms:
            if t in low:
                findings.append((lineno, "private term from private_terms.local.txt"))
    return findings


def main(paths: list[str]) -> int:
    terms = load_terms()
    bad = 0
    for p in paths:
        path = Path(p)
        if not path.is_file() or path.resolve() == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary files are blocked by path rules instead
        for lineno, what in scan_text(text, terms):
            print(f"{p}:{lineno}: {what}")
            bad += 1
    if bad:
        print(f"\nprivacy-scan: {bad} finding(s). Commit refused.", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
