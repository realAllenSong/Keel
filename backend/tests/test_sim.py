from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pf.reports.lighthouses import years_to_reach
from pf.sim.engine import Event, Scenario, event_flows, simulate
from pf.sim.returns_model import Generator, ReturnModel, real_returns

BOOT = ReturnModel("bootstrap", window_start="1900-01")


def flat_history(stocks=0.10, bonds=0.04, cash=0.02, infl=0.0, months=1200) -> pd.DataFrame:
    """Zero-volatility 'history': every month earns the same nominal return."""
    m = lambda a: (1 + a) ** (1 / 12) - 1  # noqa: E731
    idx = pd.period_range("1900-01", periods=months, freq="M")
    return pd.DataFrame(
        {"stocks": m(stocks), "bonds": m(bonds), "cash": m(cash), "infl": m(infl)}, index=idx
    )


def noisy_history(months=1500, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.period_range("1900-01", periods=months, freq="M")
    cov = np.array([[0.0020, 0.0001, 0], [0.0001, 0.0004, 0], [0, 0, 1e-6]])
    r = rng.multivariate_normal([0.008, 0.004, 0.002], cov, size=months)
    return pd.DataFrame(
        {"stocks": r[:, 0], "bonds": r[:, 1], "cash": r[:, 2], "infl": 0.002}, index=idx
    )


def scen(**kw) -> Scenario:  # type: ignore[no-untyped-def]
    base = dict(
        initial=0.0,
        weights={"stocks": 1.0},
        annual_income=80_000,
        annual_spending=60_000,
        work_years=40,
        horizon_years=30,
        income_growth=0.0,
        fee=0.0,
        rebalance="monthly",
    )
    base.update(kw)
    return Scenario(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------- analytic (zero volatility)
def test_zero_vol_matches_annuity_future_value() -> None:
    s = scen()
    r = simulate(s, BOOT, flat_history(), paths=50)
    rm = 1.10 ** (1 / 12) - 1
    n = 30 * 12
    fv = (20_000 / 12) * ((1 + rm) ** n - 1) / rm  # end-of-month contributions
    assert r.terminal_median == pytest.approx(fv, rel=1e-9)
    assert r.percentiles["p5"][-1] == pytest.approx(r.percentiles["p95"][-1])  # no dispersion
    assert r.success == 1.0
    assert r.expected_return == pytest.approx(0.10, rel=1e-9)


def test_zero_vol_lighthouse_one_matches_closed_form() -> None:
    """Video example: $20k/yr saved at 10% -> lighthouse 1 at $200k."""
    r = simulate(scen(), BOOT, flat_history(), paths=10)
    assert r.lighthouse_targets[0] == pytest.approx(200_000)
    expected = years_to_reach(
        0, 20_000, 0.10, 200_000
    )  # monthly contributions, rounded up to a month
    assert r.lighthouse_median_year[0] == pytest.approx(
        expected - 1 / 12, abs=1e-9
    )  # month index is 0-based


def test_zero_vol_ruin_month_is_exact() -> None:
    """$100k, spending $10k/yr, zero real return, retired: money lasts exactly 10 years."""
    s = scen(
        initial=100_000,
        weights={"cash": 1.0},
        annual_income=0,
        annual_spending=10_000,
        work_years=0,
        horizon_years=15,
    )
    r = simulate(s, BOOT, flat_history(cash=0.0), paths=5)
    # Months 1..120 are funded; month 121 (the first month of year 11) cannot be.
    assert r.ruin_by_year[10] == 0.0 and r.ruin_by_year[11] == 1.0
    assert r.success == 0.0


def test_inflation_is_removed_everything_is_real() -> None:
    nominal = flat_history(stocks=0.07, infl=0.03)
    real = real_returns(nominal)
    assert (1 + real["stocks"].iloc[0]) ** 12 - 1 == pytest.approx(1.07 / 1.03 - 1)


def test_percent_withdrawal_never_ruins() -> None:
    s = scen(
        initial=1_000_000, annual_income=0, work_years=0, withdrawal="percent", horizon_years=40
    )
    r = simulate(s, ReturnModel("bootstrap", window_start="1900-01"), noisy_history(), paths=500)
    assert r.success == 1.0


def test_events_and_repeats() -> None:
    f = event_flows(
        [Event(year=1, amount=-30_000, every=8, times=3), Event(year=0.5, amount=5000)], 12 * 30
    )
    assert f[12] == -30_000 and f[12 * 9] == -30_000 and f[12 * 17] == -30_000
    assert f[6] == 5000 and f.sum() == -85_000
    s = scen(
        initial=0,
        weights={"cash": 1.0},
        annual_income=0,
        annual_spending=0,
        events=[Event(year=2, amount=1000)],
    )
    r = simulate(s, BOOT, flat_history(cash=0.0), paths=3)
    assert r.terminal_median == pytest.approx(1000)


# ---------------------------------------------------------------- stochastic models
def test_seed_reproducible_and_common_random_numbers() -> None:
    h = noisy_history()
    s = scen(initial=100_000)
    a = simulate(s, BOOT, h, paths=300, seed=11)
    b = simulate(s, BOOT, h, paths=300, seed=11)
    c = simulate(s, BOOT, h, paths=300, seed=12)
    assert np.array_equal(a.terminal, b.terminal)
    assert not np.array_equal(a.terminal, c.terminal)
    # More spending under the SAME markets is worse on every single path.
    d = simulate(scen(initial=100_000, annual_spending=65_000), BOOT, h, paths=300, seed=11)
    assert np.all(d.terminal <= a.terminal + 1e-6)


def test_bootstrap_preserves_mean_and_volatility() -> None:
    h = noisy_history(months=2000)
    g = Generator(ReturnModel("bootstrap", window_start="1900-01"), h, paths=4000, seed=1)
    draws = np.concatenate(list(g.months(120)))
    hist = real_returns(h).to_numpy()
    assert draws.mean(0) == pytest.approx(hist.mean(0), abs=4e-4)
    assert draws.std(0) == pytest.approx(hist.std(0), rel=0.03)


def test_bootstrap_keeps_runs_contiguous() -> None:
    h = noisy_history(months=600)
    g = Generator(ReturnModel("bootstrap", window_start="1900-01", block=24), h, paths=2000, seed=3)
    seq = np.stack(list(g.months(2)))[:, :, 0]
    hist = real_returns(h)["stocks"].to_numpy()
    pos0 = np.array([np.where(hist == v)[0][0] for v in seq[0]])
    continued = seq[1] == hist[(pos0 + 1) % len(hist)]
    assert continued.mean() == pytest.approx(1 - 1 / 24, abs=0.02)


def test_student_t_matches_covariance_and_has_fat_tails() -> None:
    h = noisy_history(months=2000)
    g = Generator(ReturnModel("student_t", window_start="1900-01", df=5), h, paths=20000, seed=2)
    draws = np.concatenate(list(g.months(10)))
    hist = real_returns(h).to_numpy()
    assert np.cov(draws, rowvar=False)[0, 0] == pytest.approx(
        np.cov(hist, rowvar=False)[0, 0], rel=0.05
    )
    z = (draws[:, 0] - draws[:, 0].mean()) / draws[:, 0].std()
    assert (z**4).mean() > 4.0  # kurtosis well above a normal's 3


def test_student_t_overrides_and_shift() -> None:
    h = noisy_history()
    g = Generator(
        ReturnModel(
            "student_t", window_start="1900-01", mean={"stocks": 0.05}, vol={"stocks": 0.20}
        ),
        h,
        40000,
        4,
    )
    d = np.concatenate(list(g.months(12)))[:, 0]
    assert (1 + d.mean()) ** 12 - 1 == pytest.approx(0.05, abs=0.006)
    assert d.std() * np.sqrt(12) == pytest.approx(0.20, rel=0.04)


def test_replay_is_the_historical_path() -> None:
    h = noisy_history()
    s = scen(initial=1000, annual_income=0, annual_spending=0, horizon_years=5)
    r = simulate(
        s, ReturnModel("replay", window_start="1900-01", replay_start="1950-01"), h, paths=1
    )
    seg = real_returns(h).loc["1950-01":"1954-12", "stocks"].to_numpy()
    assert r.terminal_median == pytest.approx(1000 * np.prod(1 + seg))


def test_drawdown_measures_investment_not_cash_flows() -> None:
    # Flat 0% market but heavy spending: wealth falls, investment drawdown must be 0.
    s = scen(
        initial=500_000,
        weights={"cash": 1.0},
        annual_income=0,
        annual_spending=40_000,
        work_years=0,
        horizon_years=10,
    )
    r = simulate(s, BOOT, flat_history(cash=0.0), paths=5)
    assert r.mdd_quantiles["p95"] == 0.0
    assert r.percentiles["p50"][-1] == pytest.approx(100_000)


def test_bad_weights_rejected() -> None:
    with pytest.raises(ValueError):
        simulate(scen(weights={"stocks": 0.7}), BOOT, flat_history(), paths=2)


# ---------------------------------------------------------------- retirement account cash flows
def test_retirement_contributions_grow_and_stop_when_work_stops() -> None:
    s = scen(
        annual_income=60_000,
        annual_spending=60_000,
        retirement_spending=0,
        work_years=1,
        horizon_years=2,
        annual_retirement_contribution=12_000,
        retirement_access_year=99,
        retirement_withdrawal_tax_rate=0.2,
    )
    r = simulate(s, BOOT, flat_history(), paths=3)
    rm = 1.10 ** (1 / 12) - 1
    first_year = 1000 * ((1 + rm) ** 12 - 1) / rm
    assert r.percentiles["p50"][1] == pytest.approx(first_year)
    assert r.terminal_median == pytest.approx(first_year * 1.10)
    assert r.lighthouse_targets[0] == pytest.approx(120_000)
    assert r.success == 1.0


def test_retirement_withdrawals_use_liquid_assets_first_then_gross_up_tax() -> None:
    s = scen(
        initial=18_000,
        retirement_balance=12_000,
        retirement_withdrawal_tax_rate=0.25,
        annual_income=0,
        annual_spending=12_000,
        work_years=0,
        horizon_years=1,
        weights={"cash": 1.0},
    )
    r = simulate(s, BOOT, flat_history(cash=0.0), paths=3)
    # $6,000 liquid + $8,000 gross retirement funds $12,000 net spending.
    assert r.terminal_median == pytest.approx(4000)
    assert r.success == 1.0


def test_retirement_withdrawal_tax_can_exhaust_a_gross_balance_exactly() -> None:
    s = scen(
        initial=15_000,
        retirement_balance=15_000,
        retirement_withdrawal_tax_rate=0.2,
        annual_income=0,
        annual_spending=12_000,
        work_years=0,
        horizon_years=2,
        weights={"cash": 1.0},
    )
    r = simulate(s, BOOT, flat_history(cash=0.0), paths=3)
    assert r.percentiles["p50"][1] == pytest.approx(0, abs=1e-8)
    assert r.ruin_by_year == [0.0, 0.0, 1.0]


def test_retirement_tax_does_not_tax_cash_income_pension_or_inflows() -> None:
    s = scen(
        initial=12_000,
        retirement_balance=12_000,
        retirement_withdrawal_tax_rate=0.25,
        annual_income=24_000,
        annual_spending=12_000,
        pension=1200,
        pension_start_year=0,
        events=[Event(year=0.5, amount=1200)],
        work_years=2,
        horizon_years=1,
        weights={"cash": 1.0},
    )
    r = simulate(s, BOOT, flat_history(cash=0.0), paths=3)
    assert r.terminal_median == pytest.approx(26_400)
    assert r.success == 1.0


def test_lifecycle_outflows_also_gross_up_only_the_retirement_portion() -> None:
    s = scen(
        initial=20_000,
        retirement_balance=15_000,
        retirement_withdrawal_tax_rate=0.25,
        annual_income=0,
        annual_spending=0,
        work_years=0,
        horizon_years=1,
        weights={"cash": 1.0},
        events=[Event(year=0, amount=-8000), Event(year=0.5, amount=2000)],
    )
    r = simulate(s, BOOT, flat_history(cash=0.0), paths=3)
    assert r.terminal_median == pytest.approx(13_000)
    assert r.success == 1.0


def test_retirement_access_starts_at_the_requested_month() -> None:
    s = scen(
        initial=18_000,
        retirement_balance=12_000,
        retirement_withdrawal_tax_rate=0.25,
        retirement_access_year=0.5,
        annual_income=0,
        annual_spending=12_000,
        work_years=0,
        horizon_years=1,
        weights={"cash": 1.0},
    )
    r = simulate(s, BOOT, flat_history(cash=0.0), paths=3)
    assert r.terminal_median == pytest.approx(4000)
    assert r.success == 1.0


def test_liquidity_shortfall_retains_locked_assets_and_never_resets_failure() -> None:
    s = scen(
        initial=12_000,
        retirement_balance=12_000,
        retirement_access_year=1,
        annual_income=0,
        annual_spending=0,
        work_years=0,
        horizon_years=2,
        events=[Event(year=0, amount=-1000), Event(year=1, amount=-1000)],
    )
    r = simulate(s, BOOT, flat_history(), paths=3)
    assert r.ruin_by_year == [0.0, 1.0, 1.0]
    assert r.percentiles["p50"][1] == pytest.approx(13_200)
    # The missed payment is not invented debt; the later payment is funded after access opens.
    assert r.terminal_median == pytest.approx(14_520 - 1000 * 1.10 ** (11 / 12))
    assert r.success == 0.0


def test_locked_contributions_cannot_cover_working_cash_shortfalls() -> None:
    s = scen(
        annual_income=0,
        annual_spending=1200,
        annual_retirement_contribution=12_000,
        retirement_access_year=2,
        work_years=1,
        horizon_years=1,
        weights={"cash": 1.0},
    )
    r = simulate(s, BOOT, flat_history(cash=0.0), paths=3)
    assert r.success == 0.0
    assert r.terminal_median == pytest.approx(12_000)


def test_full_retirement_tax_rate_cannot_produce_spendable_cash() -> None:
    s = scen(
        initial=12_000,
        retirement_balance=12_000,
        retirement_withdrawal_tax_rate=1.0,
        annual_income=0,
        annual_spending=1000,
        work_years=0,
        horizon_years=1,
        weights={"cash": 1.0},
    )
    r = simulate(s, BOOT, flat_history(cash=0.0), paths=3)
    assert r.success == 0.0
    assert r.terminal_median == pytest.approx(12_000)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("annual_retirement_contribution", -1),
        ("annual_retirement_contribution", float("inf")),
        ("retirement_balance", -1),
        ("retirement_balance", 1001),
        ("retirement_withdrawal_tax_rate", -0.1),
        ("retirement_withdrawal_tax_rate", 1.1),
        ("retirement_withdrawal_tax_rate", float("nan")),
        ("retirement_access_year", -1),
        ("retirement_access_year", float("inf")),
    ],
)
def test_invalid_retirement_assumptions_rejected(field: str, value: float) -> None:
    with pytest.raises(ValueError, match=field):
        simulate(scen(initial=1000, **{field: value}), BOOT, flat_history(), paths=2)


@pytest.mark.parametrize("rebalance", ["monthly", "annual", "none"])
@pytest.mark.parametrize("kind", ["bootstrap", "student_t", "replay"])
def test_retirement_pools_keep_market_allocation_and_investment_drawdowns(
    rebalance: str, kind: str
) -> None:
    common = dict(
        initial=100_000,
        weights={"stocks": 0.6, "bonds": 0.3, "cash": 0.1},
        annual_income=0,
        annual_spending=0,
        work_years=0,
        horizon_years=3,
        rebalance=rebalance,
        fee=0.002,
    )
    model = ReturnModel(kind, window_start="1900-01", replay_start="1950-01")
    h = noisy_history()
    legacy = simulate(scen(**common), model, h, paths=5, seed=11)
    retirement = simulate(scen(**common, retirement_balance=40_000), model, h, paths=5, seed=11)
    assert retirement.terminal == pytest.approx(legacy.terminal, rel=1e-12)
    assert np.array_equal(retirement.mdd, legacy.mdd)


def test_retirement_percent_spending_is_net_and_grosses_up_each_month() -> None:
    s = scen(
        initial=120_000,
        retirement_balance=120_000,
        retirement_withdrawal_tax_rate=0.2,
        annual_income=0,
        work_years=0,
        horizon_years=1,
        weights={"cash": 1.0},
        withdrawal="percent",
        withdrawal_rate=0.04,
    )
    r = simulate(s, BOOT, flat_history(cash=0.0), paths=3)
    assert r.terminal_median == pytest.approx(120_000 * (1 - 0.04 / 12 / 0.8) ** 12)
    assert r.success == 1.0


def test_retirement_guardrails_adjust_net_spending_then_gross_up_tax() -> None:
    s = scen(
        initial=30_000,
        retirement_balance=30_000,
        retirement_withdrawal_tax_rate=0.2,
        annual_income=0,
        annual_spending=12_000,
        work_years=0,
        horizon_years=2,
        weights={"cash": 1.0},
        withdrawal="guardrails",
        withdrawal_rate=0.1,
    )
    r = simulate(s, BOOT, flat_history(cash=0.0), paths=3)
    assert r.percentiles["p50"][1] == pytest.approx(15_000)
    assert r.terminal_median == pytest.approx(1500)
    assert r.success == 1.0


def test_zero_retirement_defaults_preserve_existing_seeded_paths_exactly() -> None:
    s = scen(
        initial=40_000,
        weights={"stocks": 0.6, "bonds": 0.3, "cash": 0.1},
        annual_income=30_000,
        annual_spending=18_000,
        work_years=1.5,
        horizon_years=5,
        fee=0.002,
        rebalance="annual",
        events=[Event(year=2, amount=-10_000)],
        annual_retirement_contribution=0,
        retirement_balance=0,
        retirement_withdrawal_tax_rate=0,
        retirement_access_year=0,
    )
    r = simulate(s, BOOT, noisy_history(), paths=6, seed=11)
    assert np.array_equal(r.terminal, [9405.521851854719, 7146.448152391009, 0, 0, 0, 0])
    assert r.ruin_by_year == [0, 0, 0, 0, 0, 2 / 3]


# ---------------------------------------------------------------- analyses
from pf.sim.analysis import (  # noqa: E402
    allocation_grid,
    earliest_retirement,
    optimize,
    replays,
    retirement_curve,
    sensitivity,
)


def test_sensitivity_directions_under_common_random_numbers() -> None:
    s = scen(initial=50_000, weights={"stocks": 0.6, "bonds": 0.4}, work_years=15, horizon_years=40)
    base, _, rows = sensitivity(s, BOOT, noisy_history(), paths=400)
    by = {r.variable: r for r in rows}
    assert by["spending"].success_low >= base >= by["spending"].success_high
    assert by["spending"].median_low > by["spending"].median_high
    assert by["work_years"].median_high > by["work_years"].median_low
    assert by["returns"].median_high > by["returns"].median_low
    assert by["income"].median_high > by["income"].median_low
    assert len(rows) == 6


def test_initial_sensitivity_scales_the_retirement_portion_with_total_assets() -> None:
    s = scen(
        initial=100_000,
        retirement_balance=100_000,
        annual_retirement_contribution=12_000,
        retirement_access_year=30,
        retirement_withdrawal_tax_rate=0.2,
        annual_income=24_000,
        annual_spending=24_000,
        work_years=5,
        horizon_years=1,
        weights={"cash": 1.0},
    )
    base_success, base_median, rows = sensitivity(s, BOOT, flat_history(cash=0.0), paths=3)
    initial = next(row for row in rows if row.variable == "initial")
    assert base_success == 1.0
    assert base_median == pytest.approx(112_000)
    assert initial.median_low == pytest.approx(92_000)
    assert initial.median_high == pytest.approx(132_000)


def test_retirement_curve_zero_vol_is_exact_and_monotone() -> None:
    """Zero real return: save $20k/yr while working, then spend $60k/yr for the rest of 30 years.
    Working k years funds retirement iff 20k*k >= 60k*(30-k)  <=>  k >= 22.5 -> 23."""
    s = scen(weights={"cash": 1.0}, horizon_years=30)
    rc = retirement_curve(s, BOOT, flat_history(cash=0.0), target=1.0, max_years=30, paths=3)
    assert rc.earliest == 23
    assert rc.success == sorted(rc.success)
    # Bisection finds the same year from a handful of simulations.
    quick = earliest_retirement(s, BOOT, flat_history(cash=0.0), target=1.0, max_years=30, paths=3)
    assert quick.earliest == 23 and len(quick.years) <= 7 and 22 in quick.years


def test_replays_cover_requested_starts() -> None:
    h = noisy_history(months=1500)  # 1900-01 .. 2024-12
    rps = replays(
        scen(initial=100_000, horizon_years=30), h, starts=("1929-09", "2000-01", "2030-01")
    )
    assert [r.start for r in rps] == ["1929-09", "2000-01"]  # 2030 not in history
    assert rps[0].complete and not rps[1].complete and len(rps[1].years) == 25


def test_allocation_grid() -> None:
    g = allocation_grid(0.1)
    assert len(g) == 66 and all(abs(sum(w.values()) - 1) < 1e-9 for w in g)


def test_maximising_expected_wealth_goes_all_in_but_growth_optimal_respects_constraints() -> None:
    h = noisy_history()
    s = scen(
        initial=200_000, weights={"stocks": 0.5, "bonds": 0.5}, work_years=10, horizon_years=25
    )
    o_mean = optimize(
        s,
        BOOT,
        h,
        objective="mean",
        max_ruin=1.0,
        max_drawdown=0.99,
        max_drawdown_prob=1.0,
        step=0.25,
        paths=300,
    )
    assert o_mean.points[o_mean.best].weights["stocks"] == 1.0  # type: ignore[index]
    o = optimize(
        s,
        BOOT,
        h,
        objective="log",
        max_ruin=0.05,
        max_drawdown=0.25,
        max_drawdown_prob=0.2,
        step=0.25,
        paths=300,
    )
    if o.best is not None:
        b = o.points[o.best]
        assert b.feasible and 1 - b.success <= 0.05 and b.mdd_exceed <= 0.2
        assert all(not p.feasible or p.e_log <= b.e_log for p in o.points)


# ---------------------------------------------------------------- life stages
def test_phases_replace_steady_flows_then_career_resumes() -> None:
    from pf.sim.engine import Phase, cash_bridge

    # cash earns 0 in this history, so wealth is the plain sum of flows
    s = scen(
        weights={"cash": 1.0},
        horizon_years=2,
        phases=[
            Phase(start=0, end=5, monthly_income=0, monthly_spending=3_000, label="school"),
            Phase(start=6, end=6, monthly_income=6_000, monthly_spending=5_000, label="start"),
        ],
        events=[Event(year=1 / 12, amount=40_000, label="gift")],
        initial=10_000,
    )
    r = simulate(s, BOOT, flat_history(cash=0.0), paths=10)
    steady = (80_000 - 60_000) / 12
    year1 = 10_000 - 6 * 3_000 + 1_000 + 40_000 + 5 * steady
    assert r.percentiles["p50"][1] == pytest.approx(year1)
    rows = cash_bridge(s, 8)
    assert [x["flow"] for x in rows[:7]] == [-3000.0] * 6 + [1000.0]
    assert rows[7]["flow"] == pytest.approx(steady) and not rows[7]["phase"]
    assert rows[1]["events"] == 40_000
    assert (rows[0]["income"], rows[0]["spending"]) == (0, 3_000)
    assert rows[7]["income"] == pytest.approx(80_000 / 12)


def test_phase_overrides_retirement_draw_and_later_phase_wins() -> None:
    from pf.sim.engine import Phase, cash_bridge

    s = scen(
        work_years=0,
        retirement_spending=24_000,
        phases=[
            Phase(start=0, end=2, monthly_income=500, monthly_spending=0),
            Phase(start=2, end=3, monthly_income=0, monthly_spending=100),
        ],
    )
    assert [x["flow"] for x in cash_bridge(s, 5)] == [500, 500, -100, -100, -2000]
