"""Brokerage positions and trades beside the ledger: synthetic statements only."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection, text

from pf.ledger.importing import import_file
from pf.ledger.rebuild import rebuild
from pf.reports.portfolio import label, positions, realized, statement, trades

DAY1, DAY2 = "2026-03-02", "2026-03-03"


def _statement(
    tmp: Path, name: str, day: str, holdings: list | None, trades_: list, **extra
) -> Path:
    doc = {
        "keel_connector": 1,
        "fetched_at": f"{day}T12:00:00+00:00",
        "source": "snaptrade",
        "account_id": "acct-1",
        "institution": "Testbroker",
        "type": "Assets",
        "currency": "USD",
        "name": "Individual",
        "mask": "1234",
        "asset_class": "brokerage",
        "balances": [],
        "valuations": [{"date": day, "value": "5000.00"}],
        "transactions": [],
        "holdings": holdings or [],
        "holdings_as_of": day,
        "trades": trades_,
        **extra,
    }
    if holdings is None:
        doc["holdings_complete"] = False
    p = tmp / name
    p.write_text(json.dumps(doc))
    return p


def _t(i: int, date: str, action: str, symbol: str, qty: str, amount: str, **kw) -> dict:
    return {"id": f"t{i}", "date": date, "action": action, "symbol": symbol, "quantity": qty,
            "price": None, "amount": amount, "currency": "USD", **kw}  # fmt: skip


def _h(symbol: str, qty: str, price: str, unit_cost: str | None, **kw) -> dict:
    return {"symbol": symbol, "kind": "stock", "quantity": qty, "price": price,
            "cost_basis": unit_cost, "cost_basis_per_unit": True, "currency": "USD", **kw}  # fmt: skip


def test_positions_gain_day_change_and_cash(conn: Connection, tmp_path: Path) -> None:
    import_file(conn, _statement(tmp_path, "a.json", DAY1, [
        _h("AAA", "10", "100", "80"),
        _h("MMKT", "250", "1", "1", cash_equivalent=True),
    ], []))  # fmt: skip
    import_file(conn, _statement(tmp_path, "b.json", DAY2, [
        _h("AAA", "10", "110", "80"),
        _h("BBB", "2.5", "40", None),
        _h("MMKT", "300", "1", "1", cash_equivalent=True),
    ], []))  # fmt: skip
    h = positions(conn)
    assert (h.as_of, h.value, h.invested, h.cash) == (DAY2, "1500.00", "1200.00", "300.00")
    assert (h.cost, h.gain) == ("800.00", "300.00")  # only AAA has a known cost
    assert h.day_change == "100.00"  # AAA moved 10 a share; BBB is new, cash never moves
    acc = h.accounts[0]
    assert [p.symbol for p in acc.positions] == ["AAA", "BBB", "MMKT"]  # by value, cash last
    aaa = acc.positions[0]
    assert (aaa.quantity, aaa.value, aaa.cost, aaa.gain, aaa.day_change) == (
        "10", "1100.00", "800.00", "300.00", "100.00")  # fmt: skip
    assert aaa.gain_pct == 0.375
    assert acc.positions[1].cost is None and acc.positions[2].gain is None
    # As of the first day: the earlier report.
    assert positions(conn, dt.date.fromisoformat(DAY1)).value == "1250.00"


def test_empty_report_means_nothing_held_and_unread_positions_change_nothing(
    conn: Connection, tmp_path: Path
) -> None:
    import_file(conn, _statement(tmp_path, "a.json", DAY1, [_h("AAA", "10", "100", "80")], []))
    import_file(conn, _statement(tmp_path, "b.json", DAY2, None, []))  # positions unreadable
    assert positions(conn).as_of == DAY1 and positions(conn).value == "1000.00"
    import_file(conn, _statement(tmp_path, "c.json", "2026-03-04", [], []))  # sold everything
    h = positions(conn)
    assert (h.as_of, h.value, h.accounts[0].positions) == ("2026-03-04", "0.00", [])


def test_realized_first_in_first_out_splits_options_and_unknown_cost(
    conn: Connection, tmp_path: Path
) -> None:
    events = [
        _t(1, "2025-01-10", "BUY", "AAA", "10", "-1000"),
        _t(2, "2026-01-20", "BUY", "AAA", "10", "-1200"),
        _t(3, "2026-02-01", "SELL", "AAA", "-15", "2250"),  # 10 of lot 1, 5 of lot 2
        _t(4, "2026-02-02", "BUY", "KKK", "2", "-1580"),
        _t(5, "2026-02-10", "SPLIT", "KKK", "38", "0"),  # 2 -> 40 shares
        _t(6, "2026-02-11", "SELL", "KKK", "-20", "900"),  # half the position
        _t(
            7,
            "2026-02-12",
            "SELL",
            "NOK 260605P00014500",
            "-1",
            "14.33",
            kind="option",
            underlying="NOK",
        ),
        _t(
            8,
            "2026-02-20",
            "ASSIGNMENT",
            "NOK 260605P00014500",
            "1",
            "0",
            kind="option",
            underlying="NOK",
        ),
        _t(
            9,
            "2026-02-12",
            "BUY",
            "XYZ 260320C00015000",
            "1",
            "-50",
            kind="option",
            underlying="XYZ",
        ),
        _t(
            10,
            "2026-03-20",
            "EXPIRATION",
            "XYZ 260320C00015000",
            "1",
            "0",
            kind="option",
            underlying="XYZ",
        ),
        _t(11, "2026-02-15", "SELL", "OLD", "-3", "300"),  # bought before the history starts
        _t(12, "2026-02-16", "BUY", "MMKT", "500", "-500", kind="oef", price="1.0"),  # cash sweep
        _t(13, "2026-02-17", "REINVEST", "AAA", "0.5", "-70"),
    ]
    import_file(conn, _statement(tmp_path, "a.json", "2026-03-21", [], events))
    r = realized(conn)
    by = {(c.symbol, c.opened): c for c in r.closed}
    lot1, lot2 = by[("AAA", "2025-01-10")], by[("AAA", "2026-01-20")]
    assert (lot1.quantity, lot1.cost, lot1.proceeds, lot1.gain, lot1.long_term) == (
        "10", "1000.00", "1500.00", "500.00", True)  # fmt: skip
    assert (lot2.quantity, lot2.cost, lot2.proceeds, lot2.gain, lot2.long_term) == (
        "5", "600.00", "750.00", "150.00", False)  # fmt: skip
    kkk = by[("KKK", "2026-02-02")]
    assert (kkk.quantity, kkk.cost, kkk.gain) == ("20", "790.00", "110.00")
    put = by[("NOK 260605P00014500", "2026-02-12")]
    assert (put.name, put.cost, put.proceeds, put.gain) == (
        "NOK 2026-06-05 14.5 PUT",
        "0.00",
        "14.33",
        "14.33",
    )
    call = by[("XYZ 260320C00015000", "2026-02-12")]
    assert (call.cost, call.proceeds, call.gain) == ("50.00", "0.00", "-50.00")
    assert [(u.symbol, u.quantity, u.proceeds) for u in r.unmatched] == [("OLD", "3", "300.00")]
    assert r.by_year == {"2026": "724.33"}  # 500 + 150 + 110 + 14.33 - 50
    aaa = [x for x in r.sales if x.symbol == "AAA"]  # one sale, two lots, two holding periods
    assert [(x.quantity, x.gain, x.long_term) for x in aaa] == [
        ("5", "150.00", False),
        ("10", "500.00", True),
    ] or [(x.quantity, x.gain, x.long_term) for x in aaa] == [
        ("10", "500.00", True),
        ("5", "150.00", False),
    ]
    assert "MMKT" not in {c.symbol for c in r.closed}

    listed = trades(conn)
    assert len(listed) == 12 and listed[0].date == "2026-03-20"  # newest first, sweep left out
    assert len(trades(conn, include_cash=True)) == 13
    assert [t.symbol for t in trades(conn, symbol="nok")] == ["NOK 260605P00014500"] * 2
    assert len(trades(conn, start=dt.date(2026, 2, 12), end=dt.date(2026, 2, 12))) == 2


def test_trades_are_kept_once_and_rebuild_refills_both_tables(
    conn: Connection, tmp_path: Path
) -> None:
    first = [_t(1, DAY1, "BUY", "AAA", "10", "-1000")]
    import_file(conn, _statement(tmp_path, "a.json", DAY1, [_h("AAA", "10", "100", "100")], first))
    # The next sync overlaps a week: the same event again, plus a new one.
    again = [*first, _t(2, DAY2, "SELL", "AAA", "-4", "480")]
    import_file(conn, _statement(tmp_path, "b.json", DAY2, [_h("AAA", "6", "120", "100")], again))
    assert conn.execute(text("SELECT COUNT(*) FROM trades")).scalar_one() == 2
    before = (positions(conn), realized(conn), trades(conn))
    rebuild(conn)
    assert (positions(conn), realized(conn)) == before[:2]
    assert [(t.date, t.symbol, t.quantity) for t in trades(conn)] == [
        (t.date, t.symbol, t.quantity) for t in before[2]
    ]


def test_statement_for_a_certification(conn: Connection, tmp_path: Path) -> None:
    events = [
        _t(1, "2026-06-08", "BUY", "NOK", "100", "-1450"),
        _t(2, "2026-07-15", "SPLIT", "KKK", "38", "0"),
        _t(3, "2026-08-11", "BUY", "MMKT", "2.85", "-2.85", kind="oef", price="1"),
    ]
    import_file(
        conn, _statement(tmp_path, "a.json", "2026-09-30", [_h("NOK", "100", "15", "14.5")], events)
    )
    s = statement(conn, dt.date(2026, 7, 1), dt.date(2026, 9, 30))
    acc = s["accounts"][0]
    assert (acc["institution"], acc["number_ending"], acc["first_activity"], acc["value"]) == (
        "Testbroker", "1234", "2026-06-08", "5000.00")  # fmt: skip
    assert [t["action"] for t in s["trades"]] == [
        "SPLIT"
    ]  # only the quarter; the sweep counted apart
    assert s["cash_movements"] == 1
    assert s["holdings"]["accounts"][0]["positions"][0]["symbol"] == "NOK"


def test_option_label() -> None:
    assert label("FETH  260320P00015000") == "FETH 2026-03-20 15 PUT"
    assert label("AAPL") == "AAPL"


def test_api_and_csv(env: Path, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from pf.api.app import app
    from pf.db import make_engine

    with TestClient(app) as c:
        with make_engine().begin() as conn:
            import_file(
                conn,
                _statement(tmp_path, "a.json", "2026-09-30", [_h("NOK", "100", "15", "14.5")], [
                    _t(1, "2026-08-08", "BUY", "NOK", "100", "-1450"),
                    _t(2, "2026-09-01", "SELL", "NOK", "-40", "640"),
                ]),
            )  # fmt: skip
        h = c.get("/api/portfolio").json()
        assert (h["value"], h["gain"], h["accounts"][0]["positions"][0]["name"]) == (
            "1500.00",
            "50.00",
            "NOK",
        )
        assert [t["action"] for t in c.get("/api/portfolio/trades").json()] == ["SELL", "BUY"]
        r = c.get("/api/portfolio/realized").json()
        assert (r["by_year"], r["sales"][0]["gain"]) == ({"2026": "60.00"}, "60.00")
        text_ = c.get(
            "/api/portfolio/statement.csv", params={"start": "2026-07-01", "end": "2026-09-30"}
        ).text
        assert "Testbroker,Brokerage-1234,1234,2026-08-08,5000.00,USD" in text_
        assert "2026-09-01,Testbroker,Brokerage-1234,SELL,NOK,-40,,640.00,USD" in text_
        assert (
            c.get(
                "/api/portfolio/statement", params={"start": "2026-09-30", "end": "2026-07-01"}
            ).status_code
            == 422
        )


def test_statement_works_back_to_a_day_before_the_first_report(
    conn: Connection, tmp_path: Path
) -> None:
    events = [
        _t(1, "2026-06-05", "BUY", "KKK", "2", "-1580"),
        _t(2, "2026-07-15", "SPLIT", "KKK", "38", "0"),  # after the quarter end below
        _t(3, "2026-08-01", "BUY", "AAA", "5", "-500"),
        _t(4, "2026-08-20", "SELL", "BBB", "-3", "90"),
        _t(5, "2026-08-21", "SELL", "PUT 260905P00010000", "-1", "12", kind="option"),
    ]
    held = [_h("KKK", "40", "5", "39.5"), _h("AAA", "5", "100", "100"), _h("CCC", "7", "10", "10")]
    import_file(conn, _statement(tmp_path, "a.json", "2026-11-01", held, events))
    s = statement(conn, dt.date(2026, 4, 1), dt.date(2026, 6, 30))
    assert s["holdings"]["accounts"] == []  # no broker report that early
    back = {h["symbol"]: h["quantity"] for h in s["holdings_from_trades"]}
    # KKK before its split, AAA not bought yet, BBB still held, CCC untouched.
    assert back == {"KKK": "2", "BBB": "3", "CCC": "7"}
    # Once a report exists on or before the day, it is used as it is.
    assert (
        statement(conn, dt.date(2026, 10, 1), dt.date(2026, 11, 30))["holdings_from_trades"] == []
    )


def test_old_statement_with_value_but_no_positions_is_not_an_empty_account(
    conn: Connection, tmp_path: Path
) -> None:
    old = _statement(tmp_path, "old.json", DAY1, [], [])  # worth 5000, positions never read
    doc = json.loads(old.read_text())
    del doc["holdings_as_of"]  # as statements were written before positions were kept
    old.write_text(json.dumps(doc))
    import_file(conn, old)
    assert positions(conn).accounts == []
    # A statement written now says which day its (empty) positions are for: that is a fact.
    import_file(conn, _statement(tmp_path, "new.json", DAY2, [], []))
    assert positions(conn).accounts[0].positions == []


def test_performance_against_the_same_money_in_an_index(conn: Connection, tmp_path: Path) -> None:
    from pf.reports.portfolio import performance, symbol_history

    events = [
        _t(1, "2025-01-02", "BUY", "AAA", "10", "-1000"),
        _t(2, "2025-07-01", "SELL", "AAA", "-4", "600"),
        _t(
            3, "2025-07-01", "BUY", "MMKT", "600", "-600", kind="oef", price="1.0"
        ),  # a sweep: not a flow
    ]
    import_file(
        conn, _statement(tmp_path, "a.json", "2026-01-02", [_h("AAA", "6", "200", "100")], events)
    )
    # An index that went 100 -> 150 -> 200 over the same days.
    index = {"2025-01-02": 100.0, "2025-07-01": 150.0, "2026-01-02": 200.0}
    p = performance(conn, benchmarks=("IDX",), closes=lambda ticker, start: index)
    assert (p.since, p.as_of, p.invested, p.returned, p.value, p.gain) == (
        "2025-01-02", "2026-01-02", "1000.00", "600.00", "1200.00", "800.00")  # fmt: skip
    # 1000 bought 10 units; 600 sold 4 units at 150; 6 units left are worth 1200.
    assert [(v.ticker, v.value, v.gain) for v in p.versus] == [("IDX", "1200.00", "800.00")]
    assert p.rate is not None and p.rate == p.versus[0].rate and 0.9 < p.rate < 1.2

    # Shares the history never saw bought are left out of the value: there is no purchase to compare.
    import_file(
        conn,
        _statement(
            tmp_path,
            "b.json",
            "2026-01-03",
            [_h("AAA", "6", "200", "100"), _h("OLD", "3", "50", "40")],
            events,
        ),
    )
    assert performance(conn, benchmarks=(), closes=lambda t, s: index).value == "1200.00"

    # An index that cannot be fetched leaves the user's own numbers standing.
    def offline(ticker: str, start: object) -> dict:
        raise OSError("offline")

    q = performance(conn, benchmarks=("IDX",), closes=offline)
    assert (q.gain, q.versus, q.note) == ("800.00", [], "index prices could not be fetched")
    h = symbol_history(conn, "aaa", closes=lambda t, s: index)
    assert (h.symbol, len(h.closes), [t.action for t in h.trades]) == ("AAA", 3, ["SELL", "BUY"])


def test_tax_view_wash_sales_and_option_spreads(conn: Connection, tmp_path: Path) -> None:
    events = [
        # A loss, with the same stock bought back nine days later: half the loss is a wash sale.
        _t(1, "2026-01-05", "BUY", "AAA", "10", "-1000"),
        _t(2, "2026-03-02", "SELL", "AAA", "-10", "800"),
        _t(3, "2026-03-11", "BUY", "AAA", "5", "-450"),
        # A loss with nothing bought back near it: fully deductible.
        _t(4, "2026-01-05", "BUY", "BBB", "10", "-500"),
        _t(5, "2026-06-01", "SELL", "BBB", "-10", "400"),
        # Held over a year: long term.
        _t(6, "2024-12-01", "BUY", "CCC", "1", "-100"),
        _t(7, "2026-02-01", "SELL", "CCC", "-1", "250"),
        # A put spread: sold one strike, bought another, same underlying and expiry.
        _t(
            8,
            "2026-04-01",
            "SELL",
            "XYZ 260515P00100000",
            "-1",
            "300",
            kind="option",
            underlying="XYZ",
        ),
        _t(
            9,
            "2026-04-01",
            "BUY",
            "XYZ 260515P00090000",
            "1",
            "-120",
            kind="option",
            underlying="XYZ",
        ),
        _t(
            10,
            "2026-05-15",
            "EXPIRATION",
            "XYZ 260515P00100000",
            "1",
            "0",
            kind="option",
            underlying="XYZ",
        ),
        _t(
            11,
            "2026-05-18",
            "EXPIRATION",
            "XYZ 260515P00090000",
            "1",
            "0",
            kind="option",
            underlying="XYZ",
        ),
    ]
    import_file(
        conn, _statement(tmp_path, "a.json", "2026-07-01", [_h("AAA", "5", "90", "90")], events)
    )
    r = realized(conn)
    tax = r.tax["2026"]
    # short: AAA -200, BBB -100, spread +180 = -120; long: CCC +150; wash: half of AAA's 200.
    assert (tax.short_term, tax.long_term, tax.wash_disallowed) == ("-120.00", "150.00", "100.00")
    sale = {s.name: s for s in r.sales}
    assert sale["AAA"].wash == "100.00" and sale["BBB"].wash is None
    spread = sale["XYZ 2026-05-15"]
    assert (spread.legs, spread.date, spread.cost, spread.proceeds, spread.gain) == (
        2,
        "2026-05-18",
        "120.00",
        "300.00",
        "180.00",
    )
    assert not any(
        s.name.startswith("XYZ 2026-05-15 ") for s in r.sales
    )  # its legs are not listed apart


def test_delayed_quotes_revalue_positions_and_other_currencies_convert(
    conn: Connection, tmp_path: Path
) -> None:
    from pf.market.update import store_fx
    from pf.reports.portfolio import live_positions

    held = [
        _h("AAA", "10", "100", "80"),
        _h("MMKT", "250", "1", "1", cash_equivalent=True),
        {**_h("0700", "100", "400", "350"), "currency": "HKD"},  # a Hong Kong share
    ]
    import_file(conn, _statement(tmp_path, "a.json", DAY1, held, []))
    assert positions(conn).missing_fx == ["HKD"]  # no rate yet: said, not guessed
    store_fx(
        conn, "HKD", [(dt.date.fromisoformat(DAY1), Decimal("7.8"))]
    )  # HKD per USD, as FRED quotes it
    h = positions(conn)
    # 1000 + 250 cash + 40000 HKD / 7.8
    assert (h.missing_fx, h.value) == ([], "6378.21")

    live = live_positions(conn, fetch=lambda symbols: {"AAA": (112.5, "2026-03-02T15:45:00-05:00")})
    aaa = live.accounts[0].positions[1]
    assert (aaa.symbol, aaa.price, aaa.value, aaa.gain, aaa.day_change) == (
        "AAA",
        "112.5",
        "1125.00",
        "325.00",
        "125.00",
    )
    assert live.quoted_at == "2026-03-02T15:45:00-05:00"
    assert live.accounts[0].positions[0].price == "400"  # the HKD share keeps the broker's price

    def down(symbols: list[str]) -> dict:
        raise OSError("offline")

    assert live_positions(conn, fetch=down).quoted_at is None  # quotes failing changes nothing


def test_rates_for_another_currency_are_fetched_once_it_is_in_use(
    conn: Connection, tmp_path: Path
) -> None:
    from sqlalchemy import text as sql

    from pf.market.update import update_market

    asked: list[str] = []

    def fetch(series: str, start: object = None) -> list:
        asked.append(series)
        return [(dt.date(2026, 3, 2), Decimal("7.8") if series == "DEXHKUS" else Decimal("1.1"))]

    update_market(conn, fetch=fetch)
    assert "DEXHKUS" not in asked  # nobody holds anything in Hong Kong dollars
    import_file(
        conn,
        _statement(
            tmp_path, "a.json", DAY1, [{**_h("0700", "100", "400", "350"), "currency": "HKD"}], []
        ),
    )
    update_market(conn, fetch=fetch)
    assert "DEXHKUS" in asked and "DEXUSEU" not in asked
    rate = conn.execute(
        sql("SELECT rate FROM prices WHERE base = 'HKD' AND quote = 'USD'")
    ).scalar_one()
    assert abs(float(rate) - 1 / 7.8) < 1e-6


def test_stock_quotes_never_price_a_coin(conn: Connection, tmp_path: Path) -> None:
    from pf.reports.portfolio import live_positions

    held = [{**_h("BTC", "0.01", "90000", "80000"), "kind": "crypto"}, _h("AAA", "1", "100", "80")]
    import_file(
        conn,
        _statement(tmp_path, "a.json", DAY1, held, [], institution="Kraken", asset_class="crypto"),
    )
    live = live_positions(conn, fetch=lambda symbols: {"BTC": (31.0, "t"), "AAA": (101.0, "t")})
    assert {p.symbol: p.price for p in live.accounts[0].positions} == {"BTC": "90000", "AAA": "100"}
