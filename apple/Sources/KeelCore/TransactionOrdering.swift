import Foundation

public enum TransactionSort: String, CaseIterable, Sendable {
    case amountDescending, amountAscending, dateDescending
}

public enum TransactionOrdering {
    public static func amount(_ transaction: Txn, category: String? = nil, cashflowOnly: Bool = false) -> Decimal {
        if category != nil || cashflowOnly,
           let original = transaction.reportOriginalAmounts, original.count == 1,
           let contribution = original.values.first {
            return contribution.value
        }
        if let category { return transaction.categoryAmount(category) }
        return cashflowOnly
            ? transaction.categoryAmount("Income") - transaction.categoryAmount("Expenses")
            : transaction.amount.value
    }

    public static func baseAmount(_ transaction: Txn, category: String? = nil, cashflowOnly: Bool = false) -> Decimal? {
        convertedAmount(transaction, category: category, cashflowOnly: cashflowOnly, currency: "USD")
    }

    public static func convertedAmount(_ transaction: Txn, category: String? = nil, cashflowOnly: Bool = false, currency: String) -> Decimal? {
        if (category != nil || cashflowOnly), transaction.reportOriginalAmounts != nil {
            return transaction.displayCurrency == currency ? transaction.reportDisplayAmount?.value : nil
        }
        let rate: Decimal?
        if transaction.commodity == currency { rate = 1 }
        else if transaction.displayCurrency == currency { rate = transaction.displayRate?.value }
        else if currency == "USD" { rate = transaction.baseRate?.value }
        else { rate = nil }
        guard let rate, rate > 0 else { return nil }
        return amount(transaction, category: category, cashflowOnly: cashflowOnly) * rate
    }

    public static func sorted(_ transactions: [Txn], by order: TransactionSort, category: String? = nil, cashflowOnly: Bool = false, currency: String = "USD") -> [Txn] {
        func recent(_ a: Txn, _ b: Txn) -> Bool { a.date == b.date ? a.id > b.id : a.date > b.date }
        return transactions.sorted { a, b in
            if order == .dateDescending { return recent(a, b) }
            let av = convertedAmount(a, category: category, cashflowOnly: cashflowOnly, currency: currency)
            let bv = convertedAmount(b, category: category, cashflowOnly: cashflowOnly, currency: currency)
            // Unknown FX never turns into a made-up 1:1 conversion or a zero amount.
            if (av == nil) != (bv == nil) { return av != nil }
            if av == nil && a.commodity != b.commodity { return a.commodity < b.commodity }
            var left = av ?? amount(a, category: category, cashflowOnly: cashflowOnly)
            var right = bv ?? amount(b, category: category, cashflowOnly: cashflowOnly)
            if category == nil && !cashflowOnly { left = abs(left); right = abs(right) }
            if left == right { return recent(a, b) }
            return order == .amountDescending ? left > right : left < right
        }
    }
}
