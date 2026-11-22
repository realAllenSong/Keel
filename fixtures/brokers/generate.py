"""Rewrite the committed synthetic broker / bank / exchange exports and their ground truth.

uv run python ../fixtures/brokers/generate.py   # from backend/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from synth import household  # noqa: E402

OUT = HERE / "statements"

if __name__ == "__main__":
    for old in OUT.glob("*"):
        old.unlink()
    truth = household(OUT, seed=42)
    (HERE / "expected.json").write_text(
        json.dumps({k: v.as_json() for k, v in truth.items()}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(list(OUT.iterdir()))} files to {OUT}")
