import KeelCore
import SwiftUI

/// Loads `T` from the engine when the screen appears and whenever the ledger changes.
/// Keeps the previous value while reloading, so numbers swap in place instead of blanking. With a
/// `key`, the last value outlives the screen: switching back to a tab shows it at once and
/// refreshes underneath, instead of flashing the placeholder.
@MainActor
@Observable
final class Loader<T> {
    var value: T?
    var error: String?
    @ObservationIgnored private let key: String?

    init(_ key: String? = nil) {
        self.key = key
        value = key.flatMap { LoaderCache.values[$0] as? T }
    }

    func load(_ fetch: @escaping (API) async throws -> T) async {
        guard let api = AppModel.shared.api, !Task.isCancelled else { return }
        do {
            let v = try await fetch(api)
            try Task.checkCancellation()
            value = v
            if let key { LoaderCache.values[key] = v }
            error = nil
        } catch is CancellationError {
        } catch {
            if !Task.isCancelled { self.error = error.localizedDescription }
        }
    }

    /// Puts a value fetched together with other loaders' values on screen. Several loaders set
    /// in one synchronous block change the page in one render (a currency switch then updates
    /// numbers in place instead of collapsing to placeholders while each part reloads).
    func set(_ v: T) {
        value = v
        if let key { LoaderCache.values[key] = v }
        error = nil
    }

    func fail(_ error: Error) {
        if error is CancellationError || Task.isCancelled { return }
        self.error = error.localizedDescription
    }
}

/// The last value each keyed Loader read, for the life of the app.
@MainActor
enum LoaderCache {
    static var values: [String: Any] = [:]
}

/// One expected bill or payday: date, name with its kind underneath, amount (≈ when estimated).
struct EventRow: View {
    let event: PulseEvent
    var showAccount = false

    var body: some View {
        HStack(spacing: 12) {
            Text(Format.day(event.date))
                .font(.mono(11))
                .foregroundStyle(event.overdue ? Theme.outflow : Theme.muted)
                .frame(width: 56, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                Text(eventName(event)).font(.ui(13)).foregroundStyle(Theme.ink).lineLimit(1)
                Text(detail).font(.ui(10.5)).foregroundStyle(Theme.muted).lineLimit(1)
            }
            Spacer(minLength: 8)
            Text((event.estimated ? "≈" : "") + amount)
                .font(.mono(13))
                .foregroundStyle(event.amount.isNegative ? Theme.ink : Theme.inflow)
        }
        .padding(.vertical, 9)
    }

    private var amount: String {
        let v = event.amount.value
        return Format.money(v, digits: abs(v) >= 100 ? 0 : 2, sign: !event.amount.isNegative)
    }

    private var detail: String {
        var parts = [kindLabel(event.kind)]
        if event.cadence == "annual" { parts.append(L("每年")) }
        if event.overdue { parts.append(L("还没看到")) }
        if showAccount { parts.append(event.account.split(separator: ":").dropFirst().joined(separator: " ")) }
        return parts.joined(separator: " · ")
    }
}

/// Rows separated by hairlines.
struct Divided<Data: RandomAccessCollection, Row: View>: View where Data.Element: Identifiable {
    let data: Data
    @ViewBuilder let row: (Data.Element) -> Row

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(data.enumerated()), id: \.element.id) { i, item in
                if i > 0 { Hairline() }
                row(item)
            }
        }
    }
}

func kindLabel(_ kind: String) -> String {
    switch kind {
    case "income": return L("收入")
    case "card_payment": return L("还信用卡")
    case "transfer": return L("转账")
    default: return L("账单")
    }
}

func eventName(_ e: PulseEvent) -> String {
    if e.kind == "card_payment", let card = e.counterparty {
        return L("{card} 还款", ["card": card.split(separator: ":").dropFirst().joined(separator: " ")])
    }
    if e.kind == "income", e.category?.hasPrefix("Income:Salary") == true { return L("工资") }
    return Format.tidy(e.name)
}



struct ErrorLine: View {
    let message: String
    var body: some View {
        Text(message).font(.ui(13)).foregroundStyle(Theme.outflow)
    }
}

/// ‹ 2026 年 9 月 ›
struct MonthStepper: View {
    @Binding var month: String
    let latest: String

    var body: some View {
        HStack(spacing: 0) {
            step("chevron.left", -1, enabled: true)
            Text(Format.month(month)).font(.mono(12.5)).foregroundStyle(Theme.ink).padding(.horizontal, 8)
            step("chevron.right", 1, enabled: month < latest)
        }
        .padding(.horizontal, 4)
        .padding(.vertical, 5)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).strokeBorder(Theme.border))
    }

    private func step(_ icon: String, _ delta: Int, enabled: Bool) -> some View {
        Button { month = AppModel.shift(month, delta) } label: {
            Image(systemName: icon).font(.ui(9, .semibold)).frame(width: 18, height: 18).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(enabled ? Theme.ink : Theme.muted.opacity(0.4))
        .disabled(!enabled)
    }
}
