import Foundation

/// Historical categories are suggestions, never a promise about future recurring cash flow.
public struct PlanningBasisLine: Decodable, Sendable, Identifiable {
    public let category: String
    public let annualAmount: Double
    public let selected: Bool
    public var id: String { category }
}

public struct PlanningBasis: Decodable, Sendable {
    public let asOf: String
    public let months: Int
    public let income: [PlanningBasisLine]
    public let spending: [PlanningBasisLine]
    public var selectedIncome: Set<String> { Set(income.filter(\.selected).map(\.category)) }
    public var selectedSpending: Set<String> { Set(spending.filter(\.selected).map(\.category)) }
    public func totalIncome(_ selected: Set<String>) -> Double { total(income, selected) }
    public func totalSpending(_ selected: Set<String>) -> Double { total(spending, selected) }
    private func total(_ lines: [PlanningBasisLine], _ selected: Set<String>) -> Double {
        // Each line originates in ledger cents; sum cents to avoid floating-point budget drift.
        Double(lines.filter { selected.contains($0.category) }.reduce(Int64(0)) { $0 + Int64(($1.annualAmount * 100).rounded()) }) / 100
    }
}

public struct EmploymentComparisonSettings: Codable, Sendable, Equatable {
    public var taxContext: String?
    public var ficaExempt: Bool?
    public var treatyExemptIncome: Double?
    public var itemizedDeductions: Double?
    public var chinaGrossIncome: Double?
    public var socialInsuranceAnnual: Double?
    public var housingFundAnnual: Double?
    public var specialDeductionsAnnual: Double?
    public var otherDeductionsAnnual: Double?
    public var cnyPerUSD: Double?
    public var chinaAnnualSpending: Double?
    public var grossIncome: Double = 0
    public var retirementContribution: Double = 0
    public var filingStatus: String = "single"
    public var nycHousingMonthly: Double = 0
    public var njHousingMonthly: Double = 0
    public var nycCommuteMonthly: Double = 0
    public var njCommuteMonthly: Double = 0
    public var otherAnnualSpending: Double = 0
    public init() {}
    public static func prefill(annualSpending: Double, historicalHousing: Double, historicalTransport: Double) -> Self {
        var result = Self()
        let budget = max(0, annualSpending)
        let housing = min(budget, max(0, historicalHousing))
        let transport = min(budget - housing, max(0, historicalTransport))
        // Displayed monthly values are cents. Put the small remainder in other
        // spending so parsing the visible fields preserves the exact annual budget.
        let housingMonthly = floor(housing / 12 * 100) / 100
        let transportMonthly = floor(transport / 12 * 100) / 100
        result.nycHousingMonthly = housingMonthly
        result.njHousingMonthly = housingMonthly
        result.nycCommuteMonthly = transportMonthly
        result.njCommuteMonthly = transportMonthly
        result.otherAnnualSpending = ((budget - 12 * (housingMonthly + transportMonthly)) * 100).rounded() / 100
        return result
    }

    public func spending(residence: String) -> Double {
        otherAnnualSpending + 12 * (residence == "nj" ? njHousingMonthly + njCommuteMonthly : nycHousingMonthly + nycCommuteMonthly)
    }
}

public struct EmploymentTaxInput: Encodable, Sendable {
    public let grossIncome: Double
    public let retirementContribution: Double
    public let filingStatus: String
    public let residence: String
    public init(_ settings: EmploymentComparisonSettings, residence: String) {
        grossIncome = settings.grossIncome
        retirementContribution = settings.retirementContribution
        filingStatus = settings.filingStatus
        self.residence = residence
    }
}

public struct EmploymentTaxResult: Decodable, Sendable {
    public let taxYear: Int
    public let grossIncome: Double
    public let retirementContribution: Double
    public let federalTax: Double
    public let socialSecurity: Double
    public let medicare: Double
    public let additionalMedicare: Double
    public let nyStateTax: Double
    public let nycTax: Double
    public let njTaxBeforeCredit: Double
    public let njCredit: Double
    public let njTax: Double
    public let totalTax: Double
    public let netIncome: Double
    public let effectiveTaxRate: Double
    public let assumptions: [String]
    public let sources: [String]
}

extension API {
    public func employmentTax(_ input: EmploymentTaxInput) async throws -> EmploymentTaxResult {
        try await post("api/sim/employment-tax", input)
    }
}

/// Shared parsing for draft fields; invalid visible text must never authorize applying an old quote.
public enum PlanningAmount {
    public static func parse(_ text: String, allowNegative: Bool) -> Double? {
        guard let value = Double(text.trimmingCharacters(in: .whitespacesAndNewlines)),
              value.isFinite, abs(value) <= 1_000_000_000, allowNegative || value >= 0 else { return nil }
        return value
    }
}

public struct ChinaTaxResult: Decodable, Sendable {
    public let taxYear: Int
    public let currency: String
    public let grossIncome: Double
    public let employeeContributions: Double
    public let incomeTax: Double
    public let netIncome: Double
    public let taxableIncome: Double
    public let sources: [String]
    public let assumptions: [String]
}

private struct ChinaTaxInput: Encodable, Sendable {
    let grossIncomeCny: Double
    let socialInsuranceAnnual: Double
    let housingFundAnnual: Double
    let specialDeductionsAnnual: Double
    let otherDeductionsAnnual: Double
    init(_ s: EmploymentComparisonSettings) {
        grossIncomeCny = s.chinaGrossIncome ?? 0; socialInsuranceAnnual = s.socialInsuranceAnnual ?? 0
        housingFundAnnual = s.housingFundAnnual ?? 0; specialDeductionsAnnual = s.specialDeductionsAnnual ?? 0
        otherDeductionsAnnual = s.otherDeductionsAnnual ?? 0
    }
}

private struct NonresidentTaxInput: Encodable, Sendable {
    let grossIncome: Double
    let retirementContribution: Double
    let residence: String
    let ficaExempt: Bool
    let filingStatus: String
    let treatyExemptIncome: Double
    let itemizedDeductions: Double
    init(_ s: EmploymentComparisonSettings, residence: String) {
        grossIncome = s.grossIncome; retirementContribution = s.retirementContribution; self.residence = residence
        ficaExempt = s.ficaExempt ?? false; filingStatus = s.filingStatus
        treatyExemptIncome = s.treatyExemptIncome ?? 0; itemizedDeductions = s.itemizedDeductions ?? 0
    }
}

extension API {
    public func chinaEmploymentTax(_ input: EmploymentComparisonSettings) async throws -> ChinaTaxResult {
        try await post("api/sim/china-employment-tax", ChinaTaxInput(input))
    }
    public func nonresidentEmploymentTax(_ input: EmploymentComparisonSettings, residence: String) async throws -> EmploymentTaxResult {
        try await post("api/sim/nonresident-employment-tax", NonresidentTaxInput(input, residence: residence))
    }
}
