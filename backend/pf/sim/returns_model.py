"""Monthly REAL return generators for the three building blocks (stocks, bonds, cash).

Every generator yields one (paths x 3) array per month from a seeded numpy Generator, so two runs
with the same seed see identical markets ("common random numbers") - that is what makes
sensitivity analysis and allocation comparisons fair.

  * BlockBootstrap - stationary bootstrap (Politis & Romano 1994): resample contiguous runs of
    history with geometric run lengths (mean `block` months). Keeps momentum/crashes and the
    joint stock/bond/inflation behaviour of the same months.
  * StudentT - multivariate Student-t with `df` degrees of freedom (fat tails), mean & covariance
    estimated from history or overridden, covariance rescaled so the variance matches.
  * Replay - one deterministic historical path starting at a given month.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import pandas as pd

BLOCKS = ("stocks", "bonds", "cash")


@dataclass
class ReturnModel:
    kind: str  # bootstrap | student_t | replay
    window_start: str = "1928-01"
    window_end: str | None = None
    block: int = 12
    df: float = 5.0
    # Optional overrides (annual, REAL): {"stocks": 0.05, ...}
    mean: dict[str, float] | None = None
    vol: dict[str, float] | None = None
    shift: float = 0.0  # added to every block's annual real return (sensitivity analysis)
    replay_start: str | None = None


def real_returns(nominal: pd.DataFrame) -> pd.DataFrame:
    return (1 + nominal[list(BLOCKS)]).div(1 + nominal["infl"], axis=0) - 1


class Generator:
    def __init__(self, model: ReturnModel, history: pd.DataFrame, paths: int, seed: int) -> None:
        self.m = model
        self.paths = paths
        self.rng = np.random.default_rng(seed)
        real = real_returns(history.loc[model.window_start : model.window_end])  # type: ignore[misc]
        if len(real) < 24:
            raise ValueError("history window too short (need >= 24 months)")
        self.hist = real.to_numpy(dtype=np.float64)
        self.index = real.index
        self.monthly_shift = (1 + model.shift) ** (1 / 12) - 1

        if model.kind == "student_t":
            mu = self.hist.mean(axis=0)
            cov = np.cov(self.hist, rowvar=False)
            if model.mean:
                mu = np.array(
                    [
                        (1 + model.mean.get(b, _ann(mu[i]))) ** (1 / 12) - 1
                        for i, b in enumerate(BLOCKS)
                    ]
                )
            if model.vol:
                sd = np.sqrt(np.diag(cov))
                corr = cov / np.outer(sd, sd)
                new_sd = np.array(
                    [
                        model.vol.get(b, sd[i] * np.sqrt(12)) / np.sqrt(12)
                        for i, b in enumerate(BLOCKS)
                    ]
                )
                cov = corr * np.outer(new_sd, new_sd)
            # A t(df) draw has variance df/(df-2) * scale: shrink the scale so variance == cov.
            self.mu = mu
            self.chol = np.linalg.cholesky(cov * (model.df - 2) / model.df)
        if model.kind == "replay":
            start = model.replay_start or str(self.index[0])
            self.pos0 = int(self.index.get_loc(pd.Period(start, freq="M")))  # type: ignore[arg-type]

    def months(self, n: int) -> Iterator[np.ndarray]:
        """Yield `n` arrays of shape (paths, 3)."""
        k = self.m.kind
        if k == "bootstrap":
            yield from self._bootstrap(n)
        elif k == "student_t":
            for _ in range(n):
                z = self.rng.standard_normal((self.paths, 3)) @ self.chol.T
                w = np.sqrt(self.m.df / self.rng.chisquare(self.m.df, size=(self.paths, 1)))
                yield np.maximum(self.mu + z * w + self.monthly_shift, -0.95)
        elif k == "replay":
            h = len(self.hist)
            for t in range(n):
                i = self.pos0 + t
                if i >= h:
                    raise ValueError(
                        f"replay from {self.m.replay_start} runs past the end of history"
                    )
                yield np.broadcast_to(self.hist[i] + self.monthly_shift, (self.paths, 3))
        else:
            raise ValueError(f"unknown return model {k!r}")

    def _bootstrap(self, n: int) -> Iterator[np.ndarray]:
        h = len(self.hist)
        p_new = 1.0 / self.m.block
        pos = self.rng.integers(0, h, self.paths)
        for _ in range(n):
            yield self.hist[pos] + self.monthly_shift
            jump = self.rng.random(self.paths) < p_new
            pos = np.where(jump, self.rng.integers(0, h, self.paths), (pos + 1) % h)


def _ann(monthly: float) -> float:
    return (1 + monthly) ** 12 - 1


def history_stats(
    history: pd.DataFrame, start: str = "1928-01", end: str | None = None
) -> dict[str, dict[str, float]]:
    real = real_returns(history.loc[start:end])  # type: ignore[misc]
    out: dict[str, dict[str, float]] = {}
    for b in BLOCKS:
        r = real[b].to_numpy()
        out[b] = {
            "cagr": float(np.prod(1 + r) ** (12 / len(r)) - 1),
            "vol": float(r.std(ddof=1) * np.sqrt(12)),
        }
    return out
