import Foundation

// Responses for 花销, 资产, 规划 and the statement inbox (backend/pf/api/routers). Same rules as
// Models.swift: snake_case keys are converted, so dictionary keys with underscores arrive camelCased
// (asset class "real_estate" → "realEstate").

// MARK: - 花销

public struct ExpenseNode: Decodable, Sendable, Identifiable {
    public let category: String
    public let depth: Int
    public let amount: Money
    public let prev: Money
    public let lastYear: Money
    public let avg12: Money
    public var id: String { category }
}

public struct InsightOut: Decodable, Sendable, Identifiable {
    public let kind: String
    public let severity: String
    public let title: String
    public let body: String
    public let amount: Money?
    public let filters: [String: String]
    public var id: String { kind + title }
}

public struct OriginalFlow: Decodable, Sendable, Identifiable {
    public let currency: String
    public let income: Money
    public let expenses: Money
    public let savings: Money
    public var id: String { currency }
}

public struct CashflowTable: Decodable, Sendable {
    public let originalFlows: [OriginalFlow]?
    public let currency: String?
    public let missingFx: [String]?
    public let months: [String]
    public let income: [CashflowRow]
    public let expenses: [CashflowRow]
    public let incomeTotal: [Money]
    public let expensesTotal: [Money]
    public let savings: [Money]
    public let savingsRate: [Double?]
}

public struct CashflowRow: Decodable, Sendable, Identifiable {
    public let category: String
    public let values: [Money]
    public let total: Money
    public var id: String { category }
}

public struct DateRange: Decodable, Sendable {
    public let first: String?
    public let last: String?
}

// MARK: - 资产

public struct BalanceSheet: Decodable, Sendable {
    public let currency: String?
    public let missingFx: [String]?
    public let observedOn: [String: String]?
    public let date: String
    public let lines: [BalanceLine]
    public let byClass: [String: Money]
    public let assets: Money
    public let liabilities: Money
    public let netWorth: Money
    public func classBalance(_ key: String) -> Money? {
        byClass[key] ?? (key == "realEstate" ? byClass["real_estate"] : nil)
    }
}

public struct BalanceLine: Decodable, Sendable, Identifiable {
    public let account: String
    public let type: String
    public let assetClass: String?
    public let institution: String?
    public let balance: Money
    public let note: String?
    public let originalBalances: [String: Money]?
    public var id: String { account }
    public init(account: String, type: String, assetClass: String?, institution: String?, balance: Money) {
        self.account = account
        self.type = type
        self.assetClass = assetClass
        self.institution = institution
        self.balance = balance
        self.note = nil
        self.originalBalances = nil
    }
}

public struct LedgerAccount: Decodable, Sendable, Identifiable {
    public let name: String
    public let type: String
    public let institution: String?
    public let assetClass: String?
    public let currency: String?
    public let balance: Money
    public let commodity: String
    public let flowOnly: Bool?
    public var id: String { name }
}

/// 设置 › 规划假设: the main currency and the planning assumptions, saved by the app.
/// Dictionary keys (asset classes such as term_deposit) are not touched by the snake-case
/// strategies: they travel exactly as the engine sends them.
public struct Assumptions: Codable, Sendable, Equatable {
    public var baseCurrency: String
    public var expectedReturn: [String: Double]
    public var withdrawalRate: Double
    public var idleCashMonths: Double
    public var fiAssetClasses: [String]
}

public struct AssumptionsOut: Decodable, Sendable {
    public let current: Assumptions
    public let defaults: Assumptions
    public let currencies: [String]
}

/// A month's close: the checks, whether it's closed, and how far its numbers moved since.
public struct CloseOut: Decodable, Sendable {
    public struct Check: Decodable, Sendable {
        public let id: String
        public let ok: Bool
        public let count: Int
        public let label: String
        public let detail: String?
    }
    public let month: String
    public let ready: Bool
    public let closedAt: String?
    public let currency: String
    public let income: String
    public let expenses: String
    public let driftIncome: String
    public let driftExpenses: String
    public let checks: [Check]
}

public struct ChecklistItem: Decodable, Sendable {
    public let account: String
    public let status: String
}

public struct ImportedFile: Decodable, Sendable {
    public let account: String?
}

public struct BalanceIn: Encodable, Sendable {
    public let account: String
    public let date: String
    public let amount: String
    public let commodity: String
    public init(account: String, date: String, amount: String, commodity: String) {
        self.account = account
        self.date = date
        self.amount = amount
        self.commodity = commodity
    }
}

public struct BalanceResult: Decodable, Sendable {
    public let account: String
    public let discrepancies: [Discrepancy]
}

public struct Discrepancy: Decodable, Sendable, Identifiable {
    public let account: String
    public let date: String
    public let commodity: String
    public let statement: Money
    public let ledger: Money
    public let diff: Money
    public var id: String { account + date + commodity }
}

public struct ValuationIn: Encodable, Sendable {
    public let account: String
    public let date: String
    public let value: String
    public let commodity: String
    public init(account: String, date: String, value: String, commodity: String) {
        self.account = account
        self.date = date
        self.value = value
        self.commodity = commodity
    }
}

// MARK: - 规划 (Monte Carlo)

public struct LifeEvent: Codable, Sendable, Hashable {
    public var year: Double
    public var amount: Double
    public var label: String?
    public var every: Double?
    public var times: Int?
    /// "YYYY-MM": a dated event stays put as months pass (wins over `year`).
    public var date: String?
    public init(label: String, year: Double, amount: Double, every: Double? = nil, times: Int = 1, date: String? = nil) {
        self.label = label
        self.year = year
        self.amount = amount
        self.every = every
        self.times = times
        self.date = date
    }
}

/// A life stage in calendar months (both inclusive) with its own monthly money: school, a gap,
/// a first job. Inside it, these numbers replace the steady yearly income and spending.
public struct PlanPhase: Codable, Sendable, Hashable {
    public var label: String
    public var start: String
    public var end: String
    public var monthlyIncome: Double
    public var monthlySpending: Double
    public init(label: String, start: String, end: String, monthlyIncome: Double, monthlySpending: Double) {
        self.label = label
        self.start = start
        self.end = end
        self.monthlyIncome = monthlyIncome
        self.monthlySpending = monthlySpending
    }
}

public struct PlanGoal: Codable, Sendable, Hashable {
    public var label: String
    public var amount: Double
    public init(label: String, amount: Double) {
        self.label = label
        self.amount = amount
    }
}

public struct BridgeMonth: Decodable, Sendable, Hashable {
    public let month: String
    public let flow: Double
    public var income: Double?
    public var spending: Double?
    public let events: Double
    public let labels: [String]
    public let balance: Double
}

/// US-dollar cash month by month under the plan, without markets.
public struct BridgeOut: Decodable, Sendable {
    public let asOf: String
    public let start: Double
    public let cash: Double
    public let cards: Double
    public let other: [String: Double]
    public let rows: [BridgeMonth]
    public let low: Double
    public let lowMonth: String
    public let shortMonth: String?
}

struct BridgeIn: Encodable, Sendable {
    let scenario: Scenario
    let months: Int
}

public struct Scenario: Codable, Sendable, Equatable {
    public var initial: Double
    public var weights: [String: Double]
    public var annualIncome: Double
    public var annualSpending: Double
    public var annualRetirementContribution: Double?
    public var retirementBalance: Double?
    public var retirementWithdrawalTaxRate: Double?
    public var retirementAccessYear: Double?
    public var hasRetirementAccount: Bool { (annualRetirementContribution ?? 0) > 0 || (retirementBalance ?? 0) > 0 }
    public var workYears: Double
    public var horizonYears: Int?
    public var incomeGrowth: Double?
    public var retirementSpending: Double?
    public var pension: Double?
    public var pensionStartYear: Double?
    public var withdrawal: String?
    public var withdrawalRate: Double?
    public var rebalance: String?
    public var fee: Double?
    public var expectedReturn: Double?
    public var events: [LifeEvent]?
    public var phases: [PlanPhase]?
}

public struct MarketModel: Codable, Sendable, Equatable {
    public var kind: String?
    public var windowStart: String?
    public var windowEnd: String?
    public var block: Int?
    public var df: Double?
}

/// What the planning screen edits: the scenario and the market model (persisted on this Mac).
public struct PlanState: Codable, Sendable, Equatable {
    public var scenario: Scenario
    public var model: MarketModel
    public var retirementReviewed: Bool?
    public var cashflowReviewed: Bool?
    public var employmentComparison: EmploymentComparisonSettings?
    /// What you are saving towards (today's dollars). Kept on this Mac; the engine never sees it.
    public var goals: [PlanGoal]?
    public var projectionReady: Bool { cashflowReviewed != false && (!scenario.hasRetirementAccount || retirementReviewed == true) }
    public init(scenario: Scenario, model: MarketModel) {
        self.scenario = scenario
        self.model = model
    }
}

public struct SimDefaults: Decodable, Sendable {
    public let scenario: Scenario
    public let model: MarketModel
    public let derivedFrom: String
    public var facts: PlanFacts?
    public var planningBasis: PlanningBasis?
}

public struct FactLine: Decodable, Sendable, Hashable {
    public let category: String
    public let amount: Double
}

/// What the ledger shows about the last 12 months, with no guess about the future.
public struct PlanFacts: Decodable, Sendable {
    public let asOf: String
    public let months: Int
    public let investable: Double
    public let incomeYear: Double
    public let spendingYear: Double
    public let incomeBy: [FactLine]
    public let spendingBy: [FactLine]
    public let recentIncome: Double
    public let recentSpending: Double
}

struct RunIn: Encodable, Sendable {
    let scenario: Scenario
    let model: MarketModel
    let paths: Int
    let seed: Int
}

struct RetirementIn: Encodable, Sendable {
    let scenario: Scenario
    let model: MarketModel
    let paths: Int
    let seed: Int
    let target: Double
    let maxYears: Int
    let step: Int
    var curve = false  // just the earliest year: the engine bisects instead of simulating every year
}

struct OptimizeIn: Encodable, Sendable {
    let scenario: Scenario
    let model: MarketModel
    let paths: Int
    let seed: Int
    let step: Double
    let objective: String
    let maxRuin: Double
    let maxDrawdown: Double
    let maxDrawdownProb: Double
}

public struct SimOut: Decodable, Sendable {
    public let years: [Int]
    public let percentiles: [String: [Double]]
    public let ruinByYear: [Double]
    public let success: Double
    public let terminalMean: Double
    public let terminalMedian: Double
    public let lighthouseTargets: [Double]
    public let lighthouseProb: [Double]
    public let lighthouseMedianYear: [Double?]
    public let mddExceed: [String: Double]
    public let underwaterMonthsMedian: Double
    public let paths: Int
    public let expectedReturn: Double
}

public struct RetirementOut: Decodable, Sendable {
    public let years: [Int]
    public let success: [Double]
    public let earliest: Int?
}

public struct ReplayOut: Decodable, Sendable, Identifiable {
    public let start: String
    public let years: [Int]
    public let wealth: [Double]
    public let ruinedYear: Int?
    public let complete: Bool
    public var id: String { start }
}

public struct OptimizeOut: Decodable, Sendable {
    public let points: [AllocationPoint]
    public let best: Int?
    public let current: AllocationPoint
}

public struct AllocationPoint: Decodable, Sendable {
    public let weights: [String: Double]
    public let success: Double
    public let median: Double
}

public struct SensitivityOut: Decodable, Sendable {
    public let baseSuccess: Double
    public let rows: [SensitivityRow]
}

public struct SensitivityRow: Decodable, Sendable, Identifiable {
    public let label: String
    public let lowLabel: String
    public let highLabel: String
    public let successLow: Double
    public let successHigh: Double
    public let medianLow: Double
    public let medianHigh: Double
    public var id: String { label }
}

public struct SavedScenario: Codable, Sendable, Identifiable {
    public let name: String
    public let scenario: Scenario
    public let model: MarketModel
    public var id: String { name }
    public init(name: String, scenario: Scenario, model: MarketModel) {
        self.name = name
        self.scenario = scenario
        self.model = model
    }
}

// MARK: - backtest

struct BacktestIn: Encodable, Sendable {
    let ticker: String
    let monthly: Double
    let windows: [Int]
    let dipThreshold: Double
    let maMonths: Int
    let cashRate: Double
    let detailStart: String?
    let detailYears: Int
}

public struct BacktestOut: Decodable, Sendable {
    public let ticker: String
    public let first: String
    public let last: String
    public let labels: [String: String]
    public let windows: [BacktestWindow]
    public let detail: BacktestDetail?
}

public struct BacktestWindow: Decodable, Sendable, Identifiable {
    public let years: Int
    public let starts: [String]
    public let multiple: [String: [Double]]
    public let summary: [String: StrategySummary]
    public let dcaWinRate: [String: Double]
    public let worstStart: [String: String]
    public var id: Int { years }
}

public struct StrategySummary: Decodable, Sendable {
    public let multipleP5: Double
    public let multipleP50: Double
    public let irrP50: Double
    public let mddP50: Double
    public let mddMax: Double?  // worst drawdown over all windows (older engines omit it)
}

public struct BacktestDetail: Decodable, Sendable {
    public let start: String
    public let months: [String]
    public let contributed: [Double]
    public let value: [String: [Double]]
    public let irr: [String: Double]
    public let mdd: [String: Double]
}

// MARK: - housing calculators

public struct MortgageResult: Decodable, Sendable {
    public let payment: Double
    public let months: Int
    public let totalInterest: Double
    public let interestSaved: Double
    public let interestByYear: [Double]
    public let principalByYear: [Double]
}

public struct CashVsLoanResult: Decodable, Sendable {
    public let payment: Double
    public let advantageByYear: [Double]
    public let loanBetterAtHorizon: Bool
    public let breakevenReturn: Double
}

public struct RentVsBuyResult: Decodable, Sendable {
    public let buyNetWorth: [Double]
    public let rentNetWorth: [Double]
    public let breakevenYear: Int?
    public let monthlyCostBuyYear1: Double
    public let monthlyCostRentYear1: Double
    public let upfrontCash: Double
}

public struct RentalResult: Decodable, Sendable {
    public let cashInvested: Double
    public let noiYear1: Double
    public let capRate: Double
    public let debtServiceYear1: Double
    public let monthlyCashFlowYear1: Double
    public let cashOnCash: Double
    public let breakevenRent: Double
    public let irr: Double
    public let equityMultiple: Double
    public let tbillEquivalent: Double
    public let saleProceeds: Double
    public let cashFlowsByYear: [Double]
    public let worthIt: Bool
}

// MARK: - statement inbox

public struct PendingImport: Decodable, Sendable, Identifiable {
    public let sha256: String
    public let filename: String
    public let reason: String  // needs_account | unknown_format | needs_password
    public let importer: String?
    public let institution: String?
    public let accountType: String?
    public let hint: String?
    public let preview: StatementPreview?
    public let suggestedAccount: String?
    public var id: String { sha256 }
}

public struct StatementPreview: Decodable, Sendable {
    public let header: [String]?
    public let rows: [[String]]?
    public let guesses: ColumnGuesses?
}

public struct ColumnGuesses: Decodable, Sendable {
    public let date: String?
    public let description: String?
    public let amount: String?
    public let debit: String?
    public let credit: String?
    public let balance: String?
    public let dateFormats: [String]?
}

struct ClaimIn: Encodable, Sendable {
    let accountName: String
    let remember: Bool
}

struct UnlockIn: Encodable, Sendable {
    let password: String
    let remember: Bool
}

public struct ProfileSpec: Encodable, Sendable {
    public struct Columns: Encodable, Sendable {
        public var date: String
        public var description: String
        public var amount: String?
        public var debit: String?
        public var credit: String?
        public var balance: String?
        public init(date: String, description: String, amount: String?, debit: String?, credit: String?, balance: String?) {
            self.date = date
            self.description = description
            self.amount = amount
            self.debit = debit
            self.credit = credit
            self.balance = balance
        }
    }

    public var institution: String
    public var accountType: String
    public var assetClass: String
    public var currency: String
    public var header: [String]
    public var dateFormat: String
    public var sign: String
    public var columns: Columns

    public init(
        institution: String, accountType: String, currency: String, header: [String], dateFormat: String, sign: String,
        columns: Columns
    ) {
        self.institution = institution
        self.accountType = accountType
        self.assetClass = "cash"
        self.currency = currency
        self.header = header
        self.dateFormat = dateFormat
        self.sign = sign
        self.columns = columns
    }
}

struct TeachIn: Encodable, Sendable {
    let spec: ProfileSpec
}

public struct Institution: Decodable, Sendable, Identifiable {
    public let id: String
    public let name: String
    public let kind: String
    public let exportUrl: String
    public let steps: [String]
    public let supported: Bool
    public let note: String
}

// MARK: - endpoints

extension API {
    public func range() async throws -> DateRange { try await get("api/reports/range") }
    public func expenses(month: String, currency: String? = nil, endDate: String? = nil) async throws -> [ExpenseNode] {
        try await get("api/reports/expenses", ["month": month, "currency": currency, "end_date": endDate])
    }
    public func income(month: String, currency: String? = nil, endDate: String? = nil) async throws -> [ExpenseNode] {
        try await get("api/reports/income", ["month": month, "currency": currency, "end_date": endDate])
    }
    public func insights(month: String) async throws -> [InsightOut] { try await get("api/reports/insights", ["month": month]) }
    public func cashflow(start: String, end: String, depth: Int, currency: String = "USD", endDate: String? = nil) async throws -> CashflowTable {
        try await get("api/reports/cashflow", ["start": start, "end": end, "depth": String(depth), "currency": currency, "end_date": endDate])
    }

    public func balanceSheet(date: String? = nil, currency: String = "USD") async throws -> BalanceSheet { try await get("api/reports/balance-sheet", ["date": date, "currency": currency]) }
    public func accounts() async throws -> [LedgerAccount] { try await get("api/accounts") }
    public func assumptions() async throws -> AssumptionsOut { try await get("api/assumptions") }
    public func saveAssumptions(_ a: Assumptions) async throws -> AssumptionsOut { try await put("api/assumptions", a) }
    public func resetAssumptions() async throws -> AssumptionsOut { try await delete("api/assumptions") }
    public func closeMonth(_ month: String) async throws -> CloseOut { try await post("api/close", ["month": month]) }
    public func closeStatus(month: String) async throws -> CloseOut { try await get("api/close", ["month": month]) }
    public func checklist(month: String) async throws -> [ChecklistItem] { try await get("api/onboarding/checklist", ["month": month]) }
    public func imports() async throws -> [ImportedFile] { try await get("api/imports") }
    public func checkBalance(_ b: BalanceIn) async throws -> BalanceResult { try await post("api/accounts/balance", b) }
    public func recordValuation(_ v: ValuationIn) async throws -> Empty { try await post("api/reports/valuations", v) }

    public func transactions(
        q: String?, status: String?, month: String?, category: String?, account: String?,
        tag: String? = nil, limit: Int = 300
    ) async throws -> TxnPage {
        try await get("api/transactions", [
            "q": q, "status": status, "month": month, "category": category, "account": account,
            "tag": tag, "limit": String(limit),
        ])
    }

    // Savings goals.
    public func goals() async throws -> [Goal] { try await get("api/goals") }
    public func addGoal(_ goal: GoalIn) async throws -> [Goal] { try await post("api/goals", goal) }
    public func deleteGoal(_ id: Int) async throws -> [Goal] { try await delete("api/goals/\(id)") }

    // Tags: a second way to group lines, across categories (a trip, a move).
    public func tagTotals(currency: String = "USD") async throws -> [TagTotal] {
        try await get("api/tags", ["currency": currency])
    }
    /// Every line of this line's merchant gets the tag, now and on later imports.
    public func addTagRule(likeTxn txn: Int, tag: String) async throws {
        struct Body: Encodable { let txnId: Int; let tag: String }
        let _: [String: Int] = try await post("api/tags/rules", Body(txnId: txn, tag: tag))
    }
    public func setTags(_ txn: Int, _ tags: [String]) async throws -> [String] {
        try await put("api/transactions/\(txn)/tags", ["tags": tags])
    }

    public func simDefaults() async throws -> SimDefaults { try await get("api/sim/defaults") }
    public func simulate(_ s: PlanState, paths: Int = 10000, horizon: Int? = nil) async throws -> SimOut {
        var scenario = s.scenario
        if let horizon { scenario.horizonYears = horizon }
        return try await post("api/sim/run", RunIn(scenario: scenario, model: s.model, paths: paths, seed: 7))
    }
    public func bridge(_ s: PlanState, months: Int = 24) async throws -> BridgeOut {
        try await post("api/sim/bridge", BridgeIn(scenario: s.scenario, months: months))
    }
    public func retirement(_ s: PlanState) async throws -> RetirementOut {
        try await post("api/sim/retirement", RetirementIn(scenario: s.scenario, model: s.model, paths: 1500, seed: 7, target: 0.95, maxYears: 40, step: 1))
    }
    public func replays(_ s: PlanState) async throws -> [ReplayOut] {
        try await post("api/sim/replays", RunIn(scenario: s.scenario, model: s.model, paths: 100, seed: 7))
    }
    public func optimize(_ s: PlanState) async throws -> OptimizeOut {
        try await post("api/sim/optimize", OptimizeIn(
            scenario: s.scenario, model: s.model, paths: 2000, seed: 7, step: 0.1, objective: "log",
            maxRuin: 0.05, maxDrawdown: 0.4, maxDrawdownProb: 0.5
        ))
    }
    public func sensitivity(_ s: PlanState) async throws -> SensitivityOut {
        try await post("api/sim/sensitivity", RunIn(scenario: s.scenario, model: s.model, paths: 3000, seed: 7))
    }
    public func scenarios() async throws -> [SavedScenario] { try await get("api/sim/scenarios") }
    public func saveScenario(_ s: SavedScenario) async throws -> SavedScenario { try await put("api/sim/scenarios", s) }

    public func backtest(ticker: String, monthly: Double, dip: Double, ma: Int, detail: String?) async throws -> BacktestOut {
        try await post("api/backtest/run", BacktestIn(
            ticker: ticker, monthly: monthly, windows: [10, 20], dipThreshold: dip, maMonths: ma, cashRate: 0,
            detailStart: detail, detailYears: 10
        ))
    }

    public func calc<T: Decodable>(_ tool: String, _ values: [String: Double]) async throws -> T {
        try await post("api/calc/\(tool)", values)
    }

    public func pending() async throws -> [PendingImport] { try await get("api/onboarding/pending") }
    public func claim(_ sha: String, account: String, remember: Bool = true) async throws -> ImportResult {
        try await post("api/onboarding/pending/\(sha)/claim", ClaimIn(accountName: account, remember: remember))
    }
    public func unlock(_ sha: String, password: String, remember: Bool = false) async throws -> ImportResult {
        try await post("api/onboarding/pending/\(sha)/unlock", UnlockIn(password: password, remember: remember))
    }
    public func teach(_ sha: String, spec: ProfileSpec) async throws -> ImportResult {
        try await post("api/onboarding/pending/\(sha)/teach", TeachIn(spec: spec))
    }
    public func ignore(_ sha: String) async throws {
        let _: Empty = try await delete("api/onboarding/pending/\(sha)")
    }
    public func institutions() async throws -> [Institution] { try await get("api/onboarding/institutions") }
}
