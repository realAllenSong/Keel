import Foundation

// Mirrors of the engine's API responses (backend/pf/api). Money arrives as exact decimal strings;
// `.value` gives a Decimal for arithmetic and `.double` for charts.
// JSON keys are snake_case; the decoder converts them (avoid decoding free-form dictionaries
// whose keys contain underscores).

public struct Money: Codable, Hashable, Sendable, CustomStringConvertible {
    public let raw: String
    public init(_ raw: String) { self.raw = raw }
    public init(from decoder: Decoder) throws {
        raw = try decoder.singleValueContainer().decode(String.self)
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(raw)
    }
    public var value: Decimal { Decimal(string: raw, locale: Locale(identifier: "en_US_POSIX")) ?? 0 }
    public var double: Double { NSDecimalNumber(decimal: value).doubleValue }
    public var isNegative: Bool { raw.hasPrefix("-") }
    public var description: String { raw }
}

// MARK: - home

public struct Home: Decodable, Sendable {
    public let hasData: Bool
    public let sentence: Sentence?
    public let voyage: Voyage?
    public let todos: [Todo]
    public let moreTodos: Int
    public let firstRun: [FirstRunStep]
}

public struct Sentence: Decodable, Sendable {
    public let month: String
    public let income: Money
    public let expenses: Money
    public let savings: Money
    public let savingsRate: Double?
    public let netWorth: Money
    public let netWorthChange: Money
}

public struct Voyage: Decodable, Sendable {
    public let investable: Money
    public let wind: Money
    public let current: Money
    public let expectedReturn: Double
    public let lighthouses: [Lighthouse]
}

public struct Lighthouse: Decodable, Sendable, Identifiable {
    public let id: Int
    public let title: String
    public let short: String
    public let target: Money
    public let progress: Double
    public let reached: Bool
    public let yearsToGo: Double?
}

public struct Todo: Decodable, Sendable, Hashable {
    public let kind: String
    public let title: String
    public let detail: String?
    public let action: String
    public let href: String?
    public let external: String?
    public let count: Int
}

public struct FirstRunStep: Decodable, Sendable, Identifiable {
    public let id: String
    public let label: String
    public let done: Bool
}

// MARK: - pulse (this month so far)

public struct Pulse: Decodable, Sendable {
    public let hasData: Bool
    public let currency: String?
    public let missingFx: [String]?
    public let asOf: String?
    public let month: String?
    public let daysInMonth: Int
    public let historyMonths: Int
    public let spent: Money
    public let typicalToDate: Money
    public let typicalMonth: Money
    public let budget: Money
    public let budgetSource: String  // set | typical
    public let forecast: Money
    public let remainingBills: Money
    public let safePerDay: Money?
    public let curve: [PulseDay]
    public let upcoming: [PulseEvent]
    public let cashNow: Money
    public let cashLow: Money
    public let cashLowDate: String?
    public let lowBalance: Money
    public let cashSeries: [CashDay]
    public let alerts: [PulseAlert]
}

public struct PulseDay: Decodable, Sendable, Identifiable {
    public let day: Int
    public let actual: Money?
    public let typical: Money
    public let low: Money
    public let high: Money
    public var id: Int { day }
}

public struct PulseEvent: Decodable, Sendable, Identifiable, Hashable {
    public let date: String
    public let name: String
    public let amount: Money
    public let kind: String  // bill | income | card_payment | transfer
    public let account: String
    public let cadence: String  // monthly | annual
    public let category: String?
    public let counterparty: String?
    public let estimated: Bool
    public let overdue: Bool
    public var id: String { "\(date)|\(name)|\(account)" }
}

public struct CashDay: Decodable, Sendable, Identifiable {
    public let date: String
    public let balance: Money
    public var id: String { date }
}

/// A name that is you: wallet transfers to it move your own money (`source`: user | statement).
public struct OwnerName: Codable, Sendable, Identifiable, Hashable {
    public let name: String
    public var source: String = "user"
    public var id: String { name }
    public init(name: String, source: String = "user") {
        self.name = name
        self.source = source
    }
}

/// One day of the cash timeline: the cash accounts' closing balance, money in (income) and money
/// out (spending on any account).
public struct CashFlowDay: Decodable, Sendable, Identifiable {
    public let date: String
    public let balance: Money
    public let inflow: Money
    public let outflow: Money
    public var id: String { date }
}

public struct PulseAlert: Decodable, Sendable, Identifiable {
    public let id: String
    public let severity: String  // warning | notice | info
    public let title: String
    public let body: String
    public let href: String?
}

// MARK: - reports

public struct Overview: Decodable, Sendable {
    public let month: String
    public let currency: String?
    public let missingFx: [String]?
    public let current: MonthSummary
    public let previous: MonthSummary
    public let topExpenses: [CategoryAmount]
    public let series: [MonthSummary]
    public let insights: [Insight]
}

public struct MonthSummary: Decodable, Sendable, Identifiable {
    public let month: String
    public let income: Money
    public let expenses: Money
    public let savings: Money
    public let savingsRate: Double?
    public let netWorth: Money
    public var id: String { month }
}

public struct CategoryAmount: Decodable, Sendable, Identifiable {
    public let category: String
    public let amount: Money
    public let prev: Money
    public let avg12: Money
    public var id: String { category }
}

public struct Insight: Decodable, Sendable, Identifiable {
    public let kind: String
    public let severity: String
    public let title: String
    public let body: String
    public let amount: Money?
    public var id: String { kind + title }
}

/// One month of the balance sheet. `byClass` keys are asset classes (cash, brokerage, retirement,
/// crypto, real_estate → realEstate after key conversion, vehicle, other).
public struct NWPoint: Decodable, Sendable, Identifiable {
    public let currency: String?
    public let missingFx: [String]?
    public let month: String
    public let assets: Money
    public let liabilities: Money
    public let netWorth: Money
    public let byClass: [String: Money]
    public var id: String { month }
}

// MARK: - ledger

public struct TxnPage: Decodable, Sendable {
    public let items: [Txn]
    public let total: Int
    public let currency: String?
    public let displayTotal: Money?
    public let originalTotals: [String: Money]?
    public let missingFx: [String]?
    public init(items: [Txn], total: Int) {
        self.items = items; self.total = total
        currency = nil; displayTotal = nil; originalTotals = nil; missingFx = nil
    }
}

public struct Txn: Decodable, Sendable, Identifiable, Hashable {
    public let id: Int
    public let date: String
    public let description: String
    public let payee: String?
    public let account: String
    public let counterAccount: String?
    public let amount: Money
    public let commodity: String
    public let status: String  // pending | confirmed
    public let baseRate: Money?
    public let displayCurrency: String?
    public let displayRate: Money?
    /// Selected category legs, independent of the currency of the statement posting.
    public let reportOriginalAmounts: [String: Money]?
    public let reportDisplayAmount: Money?
    public let kind: String  // normal | transfer | ...
    public let categorySource: String?
    public let note: String?
    /// A split line: each category and its amount (category-leg sign). Empty otherwise.
    public let parts: [TxnPart]
    /// You changed this imported line (date, amount, text, split).
    public let edited: Bool
    /// Tags you put on this line ("japan-trip").
    public let tags: [String]?

    public var isPending: Bool { status == "pending" }
    public var isManual: Bool { kind == "manual" }
    public var isSplit: Bool { !parts.isEmpty }
    public var isTransfer: Bool { kind == "transfer" }
    public var canConfirm: Bool { isPending && (hasSuggestion || isSplit) }
    /// The amount attributed to this category, including descendants and refunds.
    /// Income is positive when earned; expenses are positive when spent.
    public func categoryAmount(_ category: String) -> Decimal {
        guard !isTransfer, kind != "adjustment", kind != "opening" else { return 0 }
        func matches(_ account: String) -> Bool {
            account == category || account.hasPrefix(category + ":")
        }
        let value: Decimal
        if isSplit {
            value = parts.filter { matches($0.account) }.reduce(Decimal.zero) {
                $0 + (Decimal(string: $1.amount) ?? 0)
            }
        } else {
            value = matches(counterAccount ?? "") ? -amount.value : 0
        }
        return category == "Income" || category.hasPrefix("Income:") ? -value : value
    }
    public var hasSuggestion: Bool {
        isTransfer || !(counterAccount ?? "").hasSuffix(":Uncategorized")
    }
}

public struct TxnPart: Codable, Sendable, Hashable {
    public let account: String
    public let amount: String
    public init(account: String, amount: String) {
        self.account = account
        self.amount = amount
    }
}

/// Money that moved without a statement. `amount` in the ledger sign of `account` (− = out).
public struct ManualIn: Encodable, Sendable {
    public let date: String
    public let account: String
    public let amount: String
    public let category: String
    public let description: String
    public var payee: String?
    public var note: String?
    public init(date: String, account: String, amount: String, category: String, description: String, payee: String? = nil, note: String? = nil) {
        self.date = date
        self.account = account
        self.amount = amount
        self.category = category
        self.description = description
        self.payee = payee
        self.note = note
    }
}

/// A fix to a transaction; nil fields stay as they are.
public struct EditIn: Encodable, Sendable {
    public var date: String?
    public var amount: String?
    public var description: String?
    public var payee: String?
    public var note: String?
    public init(date: String? = nil, amount: String? = nil, description: String? = nil, payee: String? = nil, note: String? = nil) {
        self.date = date
        self.amount = amount
        self.description = description
        self.payee = payee
        self.note = note
    }
}

public struct SplitIn: Encodable, Sendable {
    public let parts: [TxnPart]
}

public struct CategorizeIn: Encodable, Sendable {
    public let category: String
    public let remember: String
    public init(category: String, remember: String = "rule") {
        self.category = category
        self.remember = remember
    }
}

public struct CategorizeOut: Decodable, Sendable {
    public let alsoUpdated: Int
}

public struct IdsIn: Encodable, Sendable {
    public let ids: [Int]
    public init(ids: [Int]) { self.ids = ids }
}

public struct ImportResult: Decodable, Sendable {
    public let file: String
    public let status: String  // imported | already_imported | needs_account | unknown_format | needs_password
    public let new: Int?
    public let duplicates: Int?
    public let message: String?
    public let pendingSha: String?
}

public struct ScanResult: Decodable, Sendable {
    public let outcomes: [ScanOutcome]
}

public struct ScanOutcome: Decodable, Sendable {
    public let file: String
    public let status: String
    public let message: String?
}

// MARK: - settings & updates

public struct Prefs: Codable, Sendable, Equatable {
    public var language: String  // system | zh | en
    public var theme: String  // system | light | dark
    public var alerts: Bool
    public var monthlyBudget: String?
    public var lowBalance: String
    public var backupIcloud: Bool
    public var backgroundSync: Bool?  // nil from an engine older than the switch
    public var autoClose: Bool?  // a finished, complete month closes by itself
}

/// Where the nightly snapshots are, the newest one, how many are kept.
public struct BackupStatus: Decodable, Sendable {
    public let dir: String
    public let last: String?
    public let count: Int
    public let icloudAvailable: Bool
}

public struct VersionInfo: Decodable, Sendable {
    public let version: String?
    public let channel: String?  // release | source
    public let commit: String?
    public let branch: String?
    public let builtAt: String?
    public let sourceMode: Bool
    public let status: [String: String]  // state, step, target, log, at
}

public struct UpdateCheck: Decodable, Sendable {
    public let current: String?
    public let latest: String?
    public let behind: Int
    public let changes: [UpdateChange]
    public let canApply: Bool
    public let reason: String?
    public let error: String?
}

public struct UpdateChange: Decodable, Sendable, Identifiable {
    public let commit: String
    public let subject: String
    public let date: String
    public var id: String { commit }
}

public struct Empty: Codable, Sendable {
    public init() {}
}

// MARK: account connections

public struct ConnectionField: Codable, Sendable, Hashable {
    public let name: String
    public let label: String
    public let secret: Bool
    public let filled: Bool
}

/// One API source (Plaid, SnapTrade, IBKR Flex, OKX). Keys stay in the Keychain on the engine
/// side; this only says which fields are filled in.
public struct AccountConnection: Codable, Sendable, Identifiable, Hashable {
    public let source: String
    public let label: String
    public let connected: Bool
    public let fields: [ConnectionField]
    public var status: String?
    public var message: String?
    public var lastSync: String?
    public var accounts: Int = 0
    public var linked: [String] = []
    public var id: String { source }
}

public struct ConnectionKeys: Codable, Sendable {
    public let values: [String: String]
}

public struct SyncOutcome: Codable, Sendable {
    public let source: String
    public let ok: Bool
    public let accounts: Int
    public let new: Int
    public var message: String?
    public var reauth: Bool = false
    public var partial: Bool?
}

public struct LinkURL: Codable, Sendable {
    public let url: String
    public var linkToken: String?
}

public struct LinkToken: Codable, Sendable {
    public let linkToken: String
}

public struct LinkedBanks: Codable, Sendable {
    public let added: [String]
}

// MARK: review by merchant or person

public struct ReviewGroup: Decodable, Sendable, Identifiable, Hashable {
    /// What this group probably is and the easiest way to settle it (a card payment, a brokerage).
    public let hint: String?
    public let key: String
    public let direction: String  // out | in
    public let count: Int
    public let total: Money
    public let accounts: [String]
    public let samples: [String]
    public let txnIds: [Int]
    public let first: String
    public let last: String
    public var id: String { key + "|" + direction }
}

public struct GroupDecision: Encodable, Sendable {
    public let txnIds: [Int]
    public let category: String
    public let remember: Bool
    public init(txnIds: [Int], category: String, remember: Bool) {
        self.txnIds = txnIds
        self.category = category
        self.remember = remember
    }
}

public struct OkCount: Decodable, Sendable {
    public let count: Int
}

// MARK: - holdings and trades (brokerage accounts)

/// What brokerage accounts hold on their newest broker report.
public struct Holdings: Decodable, Sendable {
    public let currency: String
    public let asOf: String?
    public let value: Money
    public let invested: Money  // positions that are not cash
    public let cash: Money
    public let cost: Money?
    public let gain: Money?
    public let gainPct: Double?
    public let dayChange: Money?
    public let accounts: [HoldingAccount]
    public let missingFx: [String]
    /// Prices are delayed market quotes from this moment (ISO), not the broker report's.
    public let quotedAt: String?
}

public struct HoldingAccount: Decodable, Sendable, Identifiable {
    public var id: String { account }
    public let account: String
    public let institution: String?
    public let asOf: String
    public let previous: String?
    public let value: Money
    public let positions: [Position]
}

public struct Position: Decodable, Sendable, Identifiable {
    public var id: String { account + "|" + symbol }
    public let account: String
    public let symbol: String
    public let name: String  // an option in words, else the symbol
    public let kind: String?
    public let quantity: String
    public let price: String?
    public let value: Money
    public let cost: Money?
    public let gain: Money?
    public let gainPct: Double?
    public let dayChange: Money?
    public let currency: String
    public let cashEquivalent: Bool
}

public struct PortfolioTrade: Decodable, Sendable, Identifiable {
    public let id: Int
    public let date: String
    public let account: String
    public let action: String  // BUY SELL REINVEST SPLIT ASSIGNMENT EXERCISE EXPIRATION
    public let symbol: String
    public let name: String
    public let quantity: String
    public let price: String?
    public let amount: Money
    public let currency: String
}

/// Gains on closed lots, first in first out.
public struct RealizedGains: Decodable, Sendable {
    public let currency: String
    public let byYear: [String: Money]
    public let tax: [String: TaxYear]?
    public let sales: [RealizedSale]
    public let unmatched: [UnmatchedSale]
}

public struct RealizedSale: Decodable, Sendable, Identifiable {
    public var id: String { [date, account, symbol, longTerm ? "L" : "S"].joined(separator: "|") }
    public let date: String
    public let account: String
    public let symbol: String
    public let name: String
    public let quantity: String
    public let cost: Money
    public let proceeds: Money
    public let gain: Money
    public let longTerm: Bool
    public let currency: String
    public let legs: Int?  // an option position of several contracts, closed as one
    public let wash: Money?  // the part of a loss a wash sale would disallow (estimate)
}

/// A year's realised gains split the way a tax form does. An estimate.
public struct TaxYear: Decodable, Sendable {
    public let shortTerm: Money
    public let longTerm: Money
    public let washDisallowed: Money
}

/// What the money put into positions has done, and the same money in an index.
public struct Performance: Decodable, Sendable {
    public let currency: String
    public let since: String?
    public let asOf: String?
    public let invested: Money
    public let returned: Money
    public let value: Money
    public let gain: Money
    public let rate: Double?  // annualised, money-weighted
    public let dividends: [String: Money]
    public let versus: [Versus]
    public let note: String?
}

public struct Versus: Decodable, Sendable, Identifiable {
    public var id: String { ticker }
    public let ticker: String
    public let value: Money
    public let gain: Money
    public let rate: Double?
}

/// One holding's daily closes since its first trade, with its buys and sells.
public struct SymbolHistory: Decodable, Sendable {
    public let symbol: String
    public let closes: [ClosePoint]
    public let trades: [PortfolioTrade]
    public let note: String?
}

public struct ClosePoint: Decodable, Sendable, Identifiable {
    public var id: String { day }
    public let day: String
    public let close: Double
    public init(from decoder: Decoder) throws {
        var c = try decoder.unkeyedContainer()
        day = try c.decode(String.self)
        close = try c.decode(Double.self)
    }
}

/// A sale of something bought before the broker's history starts: the cost is not known.
public struct UnmatchedSale: Decodable, Sendable, Identifiable {
    public var id: String { [date, account, symbol, quantity].joined(separator: "|") }
    public let date: String
    public let account: String
    public let symbol: String
    public let name: String
    public let quantity: String
    public let proceeds: Money
    public let currency: String
}

// MARK: - bills (recurring series)

/// Recurring bills, subscriptions and income found in the ledger.
public struct Bills: Decodable, Sendable {
    public let currency: String
    public let monthlyTotal: Money  // monthly bills plus a twelfth of the annual ones
    public let yearlyTotal: Money
    public let monthly: [BillSeries]
    public let annual: [BillSeries]
    public let income: [BillSeries]
    public let dismissed: [BillSeries]
    public let missingFx: [String]
}

public struct BillSeries: Decodable, Sendable, Identifiable {
    public var id: String { key }
    public let key: String
    public let name: String
    public let account: String
    public let category: String?
    public let cadence: String  // monthly | annual
    public let day: Int
    public let month: Int?
    public let amount: Money
    public let currency: String
    public let lastDate: String
    public let nextDate: String?
    public let overdueDays: Int?
    public let subscription: Bool
    public let new: Bool  // only two charges so far
    public let priceChange: BillPriceChange?
}

public struct BillPriceChange: Decodable, Sendable {
    public let before: Money
    public let now: Money
    public let since: String
}

/// One tag with what was spent and received under it.
public struct TagTotal: Decodable, Sendable, Identifiable {
    public var id: String { tag }
    public let tag: String
    public let count: Int
    public let spent: Money
    public let income: Money
    public let first: String
    public let last: String
}

// MARK: - savings goals

public struct Goal: Decodable, Sendable, Identifiable {
    public let id: Int
    public let name: String
    public let target: Money
    public let currency: String
    public let due: String?
    public let accounts: [String]  // empty = net worth
    public let saved: Money
    public let progress: Double
    public let monthlyPace: Money
    public let reached: Bool
    public let eta: String?  // YYYY-MM at the current pace
    public let neededPerMonth: Money?
    public let onTrack: Bool?
}

public struct GoalIn: Encodable, Sendable {
    public var name: String
    public var target: String
    public var currency: String
    public var due: String?
    public var accounts: [String]
    public init(name: String, target: String, currency: String, due: String?, accounts: [String]) {
        self.name = name; self.target = target; self.currency = currency; self.due = due; self.accounts = accounts
    }
}
