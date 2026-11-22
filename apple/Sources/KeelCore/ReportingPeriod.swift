import Foundation

/// The user's reporting calendar, independent of API timestamps and window lifetime.
public enum ReportingPeriod {
    public static func day(at date: Date = Date(), timeZone: TimeZone = .current) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 2000, parts.month ?? 1, parts.day ?? 1)
    }

    public static func month(at date: Date = Date(), timeZone: TimeZone = .current) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let parts = calendar.dateComponents([.year, .month], from: date)
        return String(format: "%04d-%02d", parts.year ?? 2000, parts.month ?? 1)
    }

    public static func months(in year: Int, through currentMonth: String) -> [String] {
        (1...12).map { String(format: "%04d-%02d", year, $0) }.filter { $0 <= currentMonth }
    }

    public static func calendarEnd(currentMonth: String, forecastDates: [String]) -> String {
        max(currentMonth, forecastDates.map { String($0.prefix(7)) }.max() ?? currentMonth)
    }

    public static func end(of month: String, through today: String) -> String {
        let parts = month.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 2, (1...12).contains(parts[1]) else { return today }
        let calendar = Calendar(identifier: .gregorian)
        guard let date = calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: 1)),
              let count = calendar.range(of: .day, in: .month, for: date)?.count else { return today }
        return min(today, String(format: "%@-%02d", month, count))
    }
}

public struct FlowTotals: Sendable {
    public let income: Decimal
    public let expenses: Decimal
    public var savings: Decimal { income - expenses }
    public var rate: Double? {
        guard income > 0 else { return nil }
        return NSDecimalNumber(decimal: savings / income).doubleValue
    }
}

extension CashflowTable {
    public var totals: FlowTotals {
        FlowTotals(income: incomeTotal.reduce(0) { $0 + $1.value },
                   expenses: expensesTotal.reduce(0) { $0 + $1.value })
    }
}
