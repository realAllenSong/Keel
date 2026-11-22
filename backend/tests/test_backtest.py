from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pf.sim.backtest import Params, irr_monthly, max_drawdown, rolling, run_strategy, single


def series(values: list[float] | np.ndarray, start: str = "2000-01") -> pd.Series:
    return pd.Series(
        np.asarray(values, dtype=float), index=pd.period_range(start, periods=len(values), freq="M")
    )


def ind(px: np.ndarray, ma: int = 10) -> tuple[np.ndarray, np.ndarray]:
    s = pd.Series(px)
    return s.cummax().to_numpy(), s.rolling(ma).mean().to_numpy()


def test_constant_growth_irr_equals_growth() -> None:
    g = 0.08
    px = 100 * (1 + g) ** (np.arange(240) / 12)
    ath, sma = ind(px)
    for kind in ("dca", "lump"):
        path = run_strategy(kind, px, ath, sma, Params(monthly=500))
        assert irr_monthly(path.cashflows) == pytest.approx(g, abs=1e-6)


def test_rising_market_dip_never_triggers_and_ma_equals_dca() -> None:
    px = 100 * 1.01 ** np.arange(120)
    ath, sma = ind(px)
    p = Params(monthly=100, dip_threshold=0.1, cash_rate=0.0)
    dip = run_strategy("dip", px, ath, sma, p)
    dca = run_strategy("dca", px, ath, sma, p)
    ma = run_strategy("ma", px, ath, sma, p)
    assert dip.value[-1] == pytest.approx(100 * 120)  # all cash, never invested
    assert dca.value[-1] > dip.value[-1]
    assert ma.value[-1] == pytest.approx(dca.value[-1])  # always above its moving average
    lump = run_strategy("lump", px, ath, sma, p)
    assert lump.value[-1] > dca.value[-1]  # rising market: earlier money wins


def test_flat_market_every_strategy_returns_the_money() -> None:
    px = np.full(60, 50.0)
    ath, sma = ind(px)
    for kind in ("dca", "lump", "dip", "ma", "smart"):
        path = run_strategy(kind, px, ath, sma, Params(monthly=10))
        assert path.value[-1] == pytest.approx(600) and path.contributed[-1] == pytest.approx(600)


def test_dip_buys_after_threshold() -> None:
    px = np.array([100, 100, 85, 90, 120], dtype=float)  # 15% drawdown in month 3
    ath, sma = ind(px)
    path = run_strategy("dip", px, ath, sma, Params(monthly=100, dip_threshold=0.10))
    # cash of 300 deployed at 85 in month 3; month 4 at 90 is still 10% below ATH -> buys too.
    units = 300 / 85 + 100 / 90
    assert path.value[-1] == pytest.approx(units * 120 + 100)  # last month: 120 is a new high


def test_cash_rate_compounds_for_idle_money() -> None:
    px = 100 * 1.01 ** np.arange(24)
    ath, sma = ind(px)
    path = run_strategy("dip", px, ath, sma, Params(monthly=100, dip_threshold=0.5, cash_rate=0.12))
    m = 1.12 ** (1 / 12) - 1
    expected = sum(100 * (1 + m) ** (23 - t) for t in range(24))
    assert path.value[-1] == pytest.approx(expected)


def test_max_drawdown() -> None:
    assert max_drawdown(np.array([1, 2, 1, 3, 1.5])) == pytest.approx(0.5)
    assert max_drawdown(np.array([1, 2, 3])) == 0.0


def test_rolling_windows_and_win_rates() -> None:
    rng = np.random.default_rng(0)
    px = series(100 * np.cumprod(1 + rng.normal(0.007, 0.05, 300)))
    w = rolling(px, 10, Params())
    assert len(w.starts) == 300 - 120 + 1
    assert set(w.summary) == {"dca", "lump", "dip", "ma", "smart"}
    assert all(w.summary[k]["mdd_max"] >= w.summary[k]["mdd_p50"] for k in w.summary)
    assert all(0 <= v <= 1 for v in w.dca_win_rate.values())
    assert w.worst_start["dca"] in w.starts


def test_single_window_paths() -> None:
    px = series(100 * 1.01 ** np.arange(60))
    paths = single(px, "2001-01", 10, Params(monthly=100))  # runs to the end of data (48 months)
    assert len(paths["dca"].value) == 48 and paths["dca"].contributed[-1] == pytest.approx(4800)


def test_vectorised_engine_matches_scalar_reference() -> None:
    """The fast path must give exactly what the simple per-window loop gives."""
    from pf.sim.backtest import irr_rows, mdd_rows, run_windows

    rng = np.random.default_rng(5)
    arr = 100 * np.cumprod(1 + rng.normal(0.006, 0.06, 200))
    ath, sma = ind(arr, 6)
    n = 60
    view = np.lib.stride_tricks.sliding_window_view
    p = Params(monthly=250, dip_threshold=0.15, ma_months=6, cash_rate=0.02)
    for kind in ("dca", "lump", "dip", "ma", "smart"):
        V, C, F = run_windows(kind, view(arr, n), view(ath, n), view(sma, n), p)
        irr = irr_rows(F)
        mdd = mdd_rows(V)
        for w in (0, 17, 140):
            ref = run_strategy(kind, arr[w : w + n], ath[w : w + n], sma[w : w + n], p)
            assert np.allclose(V[w], ref.value) and np.allclose(C, ref.contributed)
            assert irr[w] == pytest.approx(irr_monthly(ref.cashflows), abs=1e-9)
            assert mdd[w] == pytest.approx(max_drawdown(ref.value))


def test_smart_dca_holds_back_near_the_high_and_spends_it_in_a_drawdown() -> None:
    px = np.array([100, 100, 80, 60, 100], dtype=float)  # dd 0, 0, 20%, 40%, 0
    ath, sma = ind(px)
    path = run_strategy("smart", px, ath, sma, Params(monthly=100))
    # month 1-2: 50 each (cash 100 left); month 3 at 20% down: 2x -> 200 (cash 0);
    # month 4 at 40% down: 3x but only 100 in hand -> 100; month 5 at the high: 50.
    units = 50 / 100 + 50 / 100 + 200 / 80 + 100 / 60 + 50 / 100
    assert path.value[-1] == pytest.approx(units * 100 + 50)
    assert path.contributed[-1] == pytest.approx(500)  # same money as dca
