"""Write a SYNTHETIC 1871-2026 monthly history (seeded) so e2e/demo runs need no network and no
third-party data. Roughly realistic moments plus a few crash runs; NOT real market data.

usage: PF_DATA_DIR=... uv run --project backend python fixtures/demo/synthetic_history.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pf.market.history import COLS, history_path

rng = np.random.default_rng(1871)
idx = pd.period_range("1871-02", "2026-09", freq="M")
n = len(idx)
infl = rng.normal(0.0022, 0.004, n)
stocks = rng.standard_t(5, n) * 0.035 + 0.0075 + infl
bonds = rng.normal(0.0035, 0.018, n) + infl * 0.5
cash = np.clip(rng.normal(0.0027, 0.0012, n), 0, None)
for start, months, drop in (("1929-09", 33, -0.06), ("1973-01", 21, -0.025), ("2000-03", 30, -0.02), ("2008-01", 14, -0.04)):
    i = idx.get_loc(pd.Period(start, freq="M"))
    stocks[i : i + months] += drop
df = pd.DataFrame({"stocks": stocks, "bonds": bonds, "cash": cash, "infl": infl}, index=idx.astype(str))
for c in COLS:
    df[f"approx_{c}"] = False
p = history_path()
p.parent.mkdir(parents=True, exist_ok=True)
df.to_parquet(p)
print(f"synthetic history -> {p} ({n} months)")
