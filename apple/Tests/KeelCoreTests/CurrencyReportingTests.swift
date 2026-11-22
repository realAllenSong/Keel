import XCTest
@testable import KeelCore

final class CurrencyReportingTests: XCTestCase {
    private func txn(_ id: Int, amount: String, native: String, usdRate: String?, quote: String?, rate: String?) throws -> Txn {
        var row: [String: Any] = ["id": id, "date": "2026-10-01", "description": "Example", "account": "Assets:Bank:Checking", "counter_account": "Expenses:Food", "amount": amount, "commodity": native, "status": "confirmed", "kind": "normal", "parts": [], "edited": false]
        row["base_rate"] = usdRate; row["display_currency"] = quote; row["display_rate"] = rate
        return try API.decoder.decode(Txn.self, from: JSONSerialization.data(withJSONObject: row))
    }

    func testSortingUsesTheSameCurrencyAsDisplayedAmounts() throws {
        let usd = try txn(1, amount: "-100", native: "USD", usdRate: "1", quote: "CNY", rate: "7.80")
        let cny = try txn(2, amount: "-750", native: "CNY", usdRate: "0.15", quote: "CNY", rate: "1")
        let missing = try txn(3, amount: "-20000", native: "EUR", usdRate: "1.1", quote: "CNY", rate: nil)
        XCTAssertEqual(TransactionOrdering.convertedAmount(usd, category: "Expenses", currency: "CNY"), 780)
        XCTAssertEqual(TransactionOrdering.sorted([cny, usd, missing], by: .amountDescending, category: "Expenses", currency: "CNY").map(\.id), [1, 2, 3])
        XCTAssertEqual(TransactionOrdering.sorted([usd, cny], by: .amountDescending, category: "Expenses").map(\.id), [2, 1])
        XCTAssertNil(TransactionOrdering.convertedAmount(missing, category: "Expenses", currency: "CNY"))
    }

    func testReportUsesConvertedFeeInsteadOfStatementLeg() throws {
        let json = #"{"id":1,"date":"2026-01-05","description":"FX transfer with fee","account":"Assets:Bank:USD","counter_account":"Assets:Bank:CNY","amount":"-101.00","commodity":"USD","status":"confirmed","kind":"transfer","parts":[],"edited":false,"display_currency":"USD","display_rate":"1","report_original_amounts":{"CNY":"-8.00"},"report_display_amount":"-1.00"}"#
        let fee = try API.decoder.decode(Txn.self, from: Data(json.utf8))
        XCTAssertEqual(TransactionOrdering.convertedAmount(fee, cashflowOnly: true, currency: "USD"), -1)
        XCTAssertEqual(fee.reportOriginalAmounts?["CNY"]?.value, -8)
        XCTAssertNil(TransactionOrdering.convertedAmount(fee, cashflowOnly: true, currency: "CNY"))
    }

    func testSummaryDecodesExactTotalAndKeepsNativeSubtotalsSeparate() throws {
        let json = #"{"items":[],"total":2,"currency":"USD","display_total":"120.53","original_totals":{"USD":"100.01","CNY":"150.00"},"missing_fx":[]}"#
        let page = try API.decoder.decode(TxnPage.self, from: Data(json.utf8))
        XCTAssertEqual(page.displayTotal?.value, Decimal(string: "120.53"))
        XCTAssertEqual(page.originalTotals?["USD"]?.value, Decimal(string: "100.01"))
        XCTAssertEqual(page.originalTotals?["CNY"]?.value, 150)
        let old = try API.decoder.decode(TxnPage.self, from: Data(#"{"items":[],"total":0}"#.utf8))
        XCTAssertNil(old.displayTotal)
    }
}
