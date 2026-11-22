import Foundation
import XCTest

@testable import KeelCore

final class FormatTests: XCTestCase {
    func testMoney() {
        XCTAssertEqual(Format.money(Decimal(string: "-1234.6")!), "−$1,235")
        XCTAssertEqual(Format.money(Decimal(string: "56.78")!), "$56.78")
        XCTAssertEqual(Format.money(Decimal(4211), sign: true), "+$4,211")
        XCTAssertEqual(Format.money(Money("88.5"), currency: "CNY"), "¥88.50")
        XCTAssertEqual(Format.money(Decimal(12), currency: "SGD", digits: 0), "SGD 12")
    }

    func testMonthAndDay() {
        XCTAssertEqual(Format.month("2026-09", lang: .en), "Sep 2026")
        XCTAssertEqual(Format.month("2026-09", lang: .en, full: true), "September")
        XCTAssertEqual(Format.month("2026-09", lang: .zh), "2026 年 9 月")
        XCTAssertEqual(Format.month("garbage", lang: .en), "garbage")
        XCTAssertEqual(Format.day("2026-10-18", lang: .en), "Oct 18")
        XCTAssertEqual(Format.day("2026-10-18", lang: .zh), "10月18日")
    }

    func testAccount() {
        XCTAssertEqual(Format.account("Expenses:Food:Dining", lang: .en), "Food › Dining")
        XCTAssertEqual(Format.account("Expenses:Food:Dining", lang: .zh), "餐饮 › 外出就餐")
        XCTAssertEqual(Format.account(nil, lang: .en), "—")
        XCTAssertEqual(Format.account("Liabilities:Chase:Card-0002", lang: .zh), "Chase › 信用卡 0002")
        XCTAssertEqual(Format.account("Assets:Bank of America:Checking-0101", lang: .en), "Bank of America › Checking 0101")
        XCTAssertEqual(Format.account("Equity:Opening-Balances", lang: .zh), "期初余额")
    }

    func testMerchantDisplayDropsCustomerServicePhonesButKeepsNamesWithDigits() {
        XCTAssertEqual(Format.tidy("OurBus 1-844-800-6828"), "OurBus")
        XCTAssertEqual(Format.tidy("OurBus (844) 800-6828"), "OurBus")
        XCTAssertEqual(Format.tidy("OurBus +1 844 800 6828"), "OurBus")
        XCTAssertEqual(Format.tidy("7-Eleven"), "7-Eleven")
        XCTAssertEqual(Format.tidy("1-800-Flowers"), "1-800-Flowers")
        XCTAssertEqual(Format.tidy("99 Ranch Market"), "99 Ranch Market")
    }

    /// The plan's facts decode with the API's own decoder (a field like 'income_12m' did not:
    /// the snake-case conversion turned it into 'income12M').
    func testPlanFactsDecode() throws {
        let json = #"""
        {"scenario": {"initial": 1, "weights": {"stocks": 1}, "annual_income": 2, "annual_spending": 1,
          "work_years": 20, "horizon_years": 50, "income_growth": 0.01, "retirement_spending": null,
          "pension": 0, "pension_start_year": 99, "withdrawal": "constant", "withdrawal_rate": 0.04,
          "rebalance": "annual", "fee": 0.001, "expected_return": null, "events": []},
         "model": {"kind": "bootstrap", "window_start": "1928-01", "window_end": null, "block": 12, "df": 5.0, "mean": null, "vol": null},
         "history": null, "derived_from": "x",
         "facts": {"as_of": "2026-09", "months": 12, "investable": 1, "income_year": 3, "spending_year": 2,
           "income_by": [{"category": "Income:Salary", "amount": 3}], "spending_by": [],
           "recent_income": 1, "recent_spending": 1}}
        """#
        let d = try API.decoder.decode(SimDefaults.self, from: Data(json.utf8))
        XCTAssertEqual(d.facts?.incomeYear, 3)
        XCTAssertEqual(d.facts?.incomeBy.first?.category, "Income:Salary")
    }

    func testMoneyDecoding() throws {
        let m = try JSONDecoder().decode([Money].self, from: Data(#"["-12.50","3"]"#.utf8))
        XCTAssertTrue(m[0].isNegative)
        XCTAssertEqual(m[0].value, Decimal(string: "-12.5"))
        XCTAssertEqual(m[1].double, 3)
    }

    func testCategoryAmountsUseOnlyMatchingSplitPartsAndKeepRefundSign() throws {
        let json = #"""
        [{"id":1,"date":"2026-08-15","description":"Mixed purchase","payee":null,
          "account":"Assets:Bank:Checking","counter_account":"Expenses:Food:Groceries",
          "amount":"-100","commodity":"USD","status":"confirmed","kind":"normal",
          "parts":[{"account":"Expenses:Food:Groceries","amount":"30"},{"account":"Expenses:Housing:Rent","amount":"70"}],"edited":true},
         {"id":2,"date":"2026-08-16","description":"Refund","payee":null,
          "account":"Assets:Bank:Checking","counter_account":"Expenses:Housing:Rent",
          "amount":"20","commodity":"USD","status":"confirmed","kind":"normal","parts":[],"edited":false},
         {"id":3,"date":"2026-08-17","description":"Salary","payee":null,
          "account":"Assets:Bank:Checking","counter_account":"Income:Salary",
          "amount":"1000","commodity":"USD","status":"confirmed","kind":"normal","parts":[],"edited":false}]
        """#
        let txns = try API.decoder.decode([Txn].self, from: Data(json.utf8))
        XCTAssertEqual(txns[0].categoryAmount("Expenses:Housing"), 70)
        XCTAssertEqual(txns[0].categoryAmount("Expenses:Food"), 30)
        XCTAssertEqual(txns[0].categoryAmount("Expenses"), 100)
        XCTAssertEqual(txns[0].categoryAmount("Expenses:House"), 0)
        XCTAssertEqual(txns[1].categoryAmount("Expenses:Housing"), -20)
        XCTAssertEqual(txns[2].categoryAmount("Income"), 1000)
        XCTAssertEqual(txns[2].categoryAmount("Expenses"), 0)
    }
}

final class ReportingTests: XCTestCase {
    private func sortTxn(_ id: Int, amount: String, date: String = "2026-10-01", currency: String = "USD", rate: String? = nil, parts: [[String: String]] = [], category: String = "Expenses:Food") throws -> Txn {
        var row: [String: Any] = ["id": id, "date": date, "description": "Example", "account": "Assets:Bank", "counter_account": category, "amount": amount, "commodity": currency, "status": "confirmed", "kind": "normal", "parts": parts, "edited": false]
        if let rate { row["base_rate"] = rate }
        return try API.decoder.decode(Txn.self, from: JSONSerialization.data(withJSONObject: row))
    }

    func testAmountOrderingUsesCategoryContributionAndSignedRefunds() throws {
        let split = try sortTxn(1, amount: "-300", parts: [["account": "Expenses:Food", "amount": "12"], ["account": "Expenses:Housing", "amount": "288"]])
        let large = try sortTxn(2, amount: "-80")
        let refund = try sortTxn(3, amount: "100")
        XCTAssertEqual(TransactionOrdering.sorted([split, refund, large], by: .amountDescending, category: "Expenses:Food").map(\.id), [2, 1, 3])
        XCTAssertEqual(TransactionOrdering.sorted([split, refund, large], by: .amountAscending, category: "Expenses:Food").map(\.id), [3, 1, 2])
    }

    func testAmountOrderingConvertsCurrenciesAndLeavesUnknownRatesAtEnd() throws {
        let yuan = try sortTxn(1, amount: "-500", currency: "CNY", rate: "0.14")
        let dollars = try sortTxn(2, amount: "-80")
        let unknown = try sortTxn(3, amount: "-100000", currency: "EUR")
        XCTAssertEqual(TransactionOrdering.sorted([unknown, yuan, dollars], by: .amountDescending, category: "Expenses").map(\.id), [2, 1, 3])
        XCTAssertEqual(TransactionOrdering.baseAmount(yuan, category: "Expenses"), Decimal(70))
        XCTAssertNil(TransactionOrdering.baseAmount(unknown, category: "Expenses"))
    }

    func testOrderingKeepsDecimalPrecisionAndStableDateTies() throws {
        let lower = try sortTxn(1, amount: "-9007199254740992.01", date: "2026-10-03")
        let higher = try sortTxn(2, amount: "-9007199254740992.02", date: "2026-10-01")
        let tied = try sortTxn(3, amount: "-9007199254740992.02", date: "2026-10-02")
        XCTAssertEqual(TransactionOrdering.sorted([lower, higher, tied], by: .amountDescending, category: "Expenses").map(\.id), [3, 2, 1])
        XCTAssertEqual(TransactionOrdering.sorted([higher, tied, lower], by: .dateDescending, category: "Expenses").map(\.id), [1, 3, 2])
    }

    func testSavingsOrderingPreservesIncomeExpenseAndRefundContributions() throws {
        let expense = try sortTxn(1, amount: "-1000")
        let income = try sortTxn(2, amount: "100", category: "Income:Salary")
        let refund = try sortTxn(3, amount: "200")
        XCTAssertEqual(TransactionOrdering.sorted([expense, income, refund], by: .amountDescending, cashflowOnly: true).map(\.id), [3, 2, 1])
        XCTAssertEqual(TransactionOrdering.sorted([expense, income, refund], by: .amountAscending, cashflowOnly: true).map(\.id), [1, 2, 3])
    }

    func testCalendarKeepsNextMonthForecastsReachableAcrossYearEnd() {
        XCTAssertEqual(ReportingPeriod.calendarEnd(currentMonth: "2026-12", forecastDates: ["2026-12-30", "2027-01-15"]), "2027-01")
        XCTAssertEqual(ReportingPeriod.calendarEnd(currentMonth: "2026-12", forecastDates: []), "2026-12")
    }

    func testAssetClassesKeepProviderDictionaryNamesAndUnknowns() throws {
        let json = #"{"date":"2026-10-31","lines":[],"by_class":{"real_estate":"100000.50","cash":"12.34"},"assets":"100012.84","liabilities":"0","net_worth":"100012.84"}"#
        let sheet = try API.decoder.decode(BalanceSheet.self, from: Data(json.utf8))
        XCTAssertEqual(sheet.classBalance("realEstate")?.value, Decimal(string: "100000.50"))
        XCTAssertEqual(sheet.classBalance("cash")?.value, Decimal(string: "12.34"))
        XCTAssertNil(sheet.classBalance("brokerage"))
    }

    func testPendingTransfersAndSplitsCanBeConfirmedWithoutRecategorizing() throws {
        let json = #"""
        [{"id":1,"date":"2026-10-01","description":"Transfer","account":"Assets:A","counter_account":"Assets:B","amount":"-100","commodity":"USD","status":"pending","kind":"transfer","parts":[],"edited":false},
         {"id":2,"date":"2026-10-01","description":"Split","account":"Assets:A","counter_account":"Expenses:Uncategorized","amount":"-100","commodity":"USD","status":"pending","kind":"normal","parts":[{"account":"Expenses:Food","amount":"100"}],"edited":false},
         {"id":3,"date":"2026-10-01","description":"Unknown","account":"Assets:A","counter_account":"Expenses:Uncategorized","amount":"-100","commodity":"USD","status":"pending","kind":"normal","parts":[],"edited":false},
         {"id":4,"date":"2026-10-01","description":"Done","account":"Assets:A","counter_account":"Expenses:Food","amount":"-100","commodity":"USD","status":"confirmed","kind":"normal","parts":[],"edited":false}]
        """#
        let items = try API.decoder.decode([Txn].self, from: Data(json.utf8))
        XCTAssertEqual(items.map(\.canConfirm), [true, true, false, false])
    }

    func testValuationsAndOpeningBalancesAreNotCashflow() throws {
        let json = #"""
        [{"id":1,"date":"2026-11-01","description":"Valuation","account":"Assets:Exchange","counter_account":"Income:Investment:Unrealized","amount":"13","commodity":"USD","status":"confirmed","kind":"adjustment","parts":[],"edited":false},
         {"id":2,"date":"2026-11-01","description":"Opening","account":"Assets:Checking","counter_account":"Income:Other","amount":"1000","commodity":"USD","status":"confirmed","kind":"opening","parts":[],"edited":false},
         {"id":3,"date":"2026-11-01","description":"Salary","account":"Assets:Checking","counter_account":"Income:Salary","amount":"100","commodity":"USD","status":"confirmed","kind":"normal","parts":[],"edited":false}]
        """#
        let txns = try API.decoder.decode([Txn].self, from: Data(json.utf8))
        XCTAssertEqual(txns.map { $0.categoryAmount("Income") }, [0, 0, 100])
    }

    func testLocalMonthYearBoundsAndLeapDay() throws {
        let date = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-11-01T02:00:00Z"))
        XCTAssertEqual(ReportingPeriod.month(at: date, timeZone: TimeZone(identifier: "America/New_York")!), "2026-10")
        XCTAssertEqual(ReportingPeriod.month(at: date, timeZone: TimeZone(secondsFromGMT: 0)!), "2026-11")
        XCTAssertEqual(ReportingPeriod.months(in: 2026, through: "2026-11"), (1...11).map { String(format: "2026-%02d", $0) })
        XCTAssertEqual(ReportingPeriod.months(in: 2025, through: "2026-11").count, 12)
        XCTAssertTrue(ReportingPeriod.months(in: 2027, through: "2026-11").isEmpty)
        XCTAssertEqual(ReportingPeriod.end(of: "2024-02", through: "2026-11-01"), "2024-02-29")
        XCTAssertEqual(ReportingPeriod.end(of: "2026-11", through: "2026-11-01"), "2026-11-01")
    }

    func testAnnualTotalsUseExactMoneyAndWeightedRate() throws {
        let json = #"""
        {"months":["2026-01","2026-02"],"income":[],"expenses":[],
         "income_total":["100.10","1.20"],"expenses_total":["50.10","100.20"],
         "savings":["50.00","-99.00"],"savings_rate":[0.4995,-82.5]}
        """#
        let flow = try API.decoder.decode(CashflowTable.self, from: Data(json.utf8))
        XCTAssertEqual(flow.totals.income, Decimal(string: "101.30"))
        XCTAssertEqual(flow.totals.expenses, Decimal(string: "150.30"))
        XCTAssertEqual(flow.totals.savings, -49)
        XCTAssertEqual(try XCTUnwrap(flow.totals.rate), -49 / 101.3, accuracy: 0.00000001)
    }
}

final class L10nTests: XCTestCase {
    func testLookupAndPlaceholders() {
        XCTAssertEqual(L("设置", lang: .en), "Settings")
        XCTAssertEqual(L("设置", lang: .zh), "设置")
        XCTAssertEqual(L("共 {n} 笔", lang: .zh, ["n": 3]), "共 3 笔")
        XCTAssertFalse(L("共 {n} 笔", lang: .en, ["n": 3]).contains("{n}"))
    }

    func testResolve() {
        XCTAssertEqual(L10n.resolve("zh"), .zh)
        XCTAssertEqual(L10n.resolve("en"), .en)
    }

    func testTidyNames() {
        XCTAssertEqual(Format.tidy("ACME CORP PAYROLL PPD ID: 9000000001"), "Acme Corp Payroll")
        XCTAssertEqual(Format.tidy("WHOLEFDS PRN 10234"), "Wholefds Prn")
        XCTAssertEqual(Format.tidy("TRADER JOE S #555"), "Trader Joe S")
        XCTAssertEqual(Format.tidy("Uber Eats"), "Uber Eats")  // already readable
        XCTAssertEqual(Format.tidy("支付宝-外卖"), "支付宝-外卖")  // l10n-allow: test data
        XCTAssertEqual(Format.tidy("12345"), "12345")  // nothing left: keep the original
    }
}
