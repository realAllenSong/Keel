import XCTest
@testable import KeelCore

final class PlanningTests: XCTestCase {
    func testLegacySavedPlanRetainsValuesAndIsNotBlocked() throws {
        let data = Data(#"{"scenario":{"initial":10000,"weights":{"cash":1},"annual_income":81560,"annual_spending":56294,"work_years":20},"model":{}}"#.utf8)
        let state = try API.decoder.decode(PlanState.self, from: data)
        XCTAssertEqual(state.scenario.annualIncome, 81560)
        XCTAssertEqual(state.scenario.annualSpending, 56294)
        XCTAssertNil(state.cashflowReviewed)
        XCTAssertTrue(state.projectionReady)
    }

    func testVisibleInvalidAmountCannotKeepAnOldQuoteApplicable() {
        for text in ["", "-100", "nan", "inf", "1e100", "garbage", "1000000001"] {
            XCTAssertNil(PlanningAmount.parse(text, allowNegative: false), text)
        }
        XCTAssertEqual(PlanningAmount.parse("0", allowNegative: false), 0)
        XCTAssertEqual(PlanningAmount.parse(" 200000 ", allowNegative: false), 200000)
        XCTAssertEqual(PlanningAmount.parse("-120.50", allowNegative: true), -120.5)
    }

    func testRetirementAssumptionsMustBeReviewedAndSurviveSave() throws {
        let data = Data(#"{"scenario":{"initial":10000,"weights":{"cash":1},"annual_income":50000,"annual_spending":40000,"work_years":20},"model":{},"cashflow_reviewed":true}"#.utf8)
        var state = try API.decoder.decode(PlanState.self, from: data)
        XCTAssertTrue(state.projectionReady)
        state.scenario.annualRetirementContribution = 20000
        XCTAssertFalse(state.projectionReady)
        state.scenario.retirementAccessYear = 25
        state.scenario.retirementWithdrawalTaxRate = 0.2
        state.retirementReviewed = true
        XCTAssertTrue(state.projectionReady)
        let restored = try API.decoder.decode(PlanState.self, from: API.encoder.encode(state))
        XCTAssertEqual(restored, state)
        XCTAssertEqual(restored.scenario.retirementWithdrawalTaxRate, 0.2)
    }

    func testMonthlyPrefillStillReconcilesAfterDisplayedCentsAreApplied() {
        let settings = EmploymentComparisonSettings.prefill(annualSpending: 65000, historicalHousing: 27029.78, historicalTransport: 1225.45)
        let displayedHousing = Double(String(format: "%.2f", settings.nycHousingMonthly))!
        let displayedTransport = Double(String(format: "%.2f", settings.nycCommuteMonthly))!
        let displayedOther = Double(String(format: "%.2f", settings.otherAnnualSpending))!
        XCTAssertEqual(displayedOther + 12 * (displayedHousing + displayedTransport), 65000, accuracy: 0.001)
    }

    func testPrefillPartitionsTransportWithoutDuplicatingIt() {
        let settings = EmploymentComparisonSettings.prefill(annualSpending: 35200, historicalHousing: 24000, historicalTransport: 1200)
        XCTAssertEqual(settings.otherAnnualSpending, 10000)
        XCTAssertEqual(settings.nycHousingMonthly, 2000)
        XCTAssertEqual(settings.nycCommuteMonthly, 100)
        XCTAssertEqual(settings.spending(residence: "nyc"), 35200)
        XCTAssertEqual(settings.spending(residence: "nj"), 35200)
        let reduced = EmploymentComparisonSettings.prefill(annualSpending: 1000, historicalHousing: 24000, historicalTransport: 1200)
        XCTAssertEqual(reduced.spending(residence: "nyc"), 1000, accuracy: 0.01)
        XCTAssertEqual(reduced.otherAnnualSpending, 0.04) // Remainder after $83.33/month housing.
    }

    func testNewPlanReviewFlagAndTaxSettingsSurviveSave() throws {
        let data = Data(#"{"scenario":{"initial":10000,"weights":{"cash":1},"annual_income":0,"annual_spending":0,"work_years":20},"model":{},"cashflow_reviewed":false}"#.utf8)
        var state = try API.decoder.decode(PlanState.self, from: data)
        XCTAssertFalse(state.projectionReady)
        var comparison = EmploymentComparisonSettings()
        comparison.grossIncome = 200000
        comparison.otherAnnualSpending = 20000
        comparison.nycHousingMonthly = 3000
        comparison.njHousingMonthly = 2500
        comparison.njCommuteMonthly = 200
        state.employmentComparison = comparison
        state.cashflowReviewed = true
        let restored = try API.decoder.decode(PlanState.self, from: API.encoder.encode(state))
        XCTAssertEqual(restored, state)
        XCTAssertTrue(restored.projectionReady)
        XCTAssertEqual(restored.employmentComparison?.spending(residence: "nj"), 52400)
        XCTAssertEqual(restored.employmentComparison?.spending(residence: "nyc"), 56000)
    }

    func testReviewedSelectionPreservesRefundsAndRequiresExplicitInclude() throws {
        let data = Data(#"{"as_of":"2026-10","months":4,"income":[{"category":"Income:Salary","annual_amount":15000.1,"selected":true},{"category":"Income:国际汇款","annual_amount":41560,"selected":false}],"spending":[{"category":"Expenses:Food","annual_amount":1200.2,"selected":true},{"category":"Expenses:Education","annual_amount":28297,"selected":false},{"category":"Expenses:Shopping","annual_amount":-200.1,"selected":true}],"suggested_income":15000.1,"suggested_spending":1000.1,"excluded_income":41560,"excluded_spending":28297}"#.utf8) // l10n-allow: fixture
        let basis = try API.decoder.decode(PlanningBasis.self, from: data)
        XCTAssertEqual(basis.totalIncome(basis.selectedIncome), 15000.1, accuracy: 0.001)
        XCTAssertEqual(basis.totalSpending(basis.selectedSpending), 1000.1, accuracy: 0.001)
        XCTAssertEqual(basis.totalIncome(["Income:国际汇款"]), 41560, accuracy: 0.001) // l10n-allow: fixture
    }
}
