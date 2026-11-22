import Charts
import KeelCore
import SwiftUI

/// The same calendar grid, with historical months and clickable expected entries.
struct CalendarView: View {
    @Environment(AppModel.self) private var model
    @State private var pulse = Loader<Pulse>("pulse")
    @State private var lines = Loader<TxnPage>()
    @State private var loadedMonth: String?

    private var month: String { model.month ?? model.currentMonth ?? ReportingPeriod.month() }

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            PageHead(title: L("日历"), sub: L("按天查看已入账收支；预计项目不计入实际金额。")) {
                MonthStepper(month: Binding(get: { month }, set: { model.month = $0 }), latest: ReportingPeriod.calendarEnd(
                    currentMonth: model.currentMonth ?? ReportingPeriod.month(), forecastDates: pulse.value?.upcoming.map(\.date) ?? []))
            }
            if let error = pulse.error ?? lines.error { ErrorLine(message: error) }
            if let p = pulse.value, loadedMonth == month, lines.error == nil {
                MonthGrid(pulse: p, month: month, days: DayTotals.byDay(lines.value?.items ?? []))
            } else { SkeletonRows(count: 7) }
        }
        .task(id: "\(model.revision)|\(month)") {
            let selected = month
            await pulse.load { try await $0.pulse() }
            await lines.load { api in
                var items: [Txn] = []
                var total = 0
                repeat {
                    let page: TxnPage = try await api.get("api/transactions", ["month": selected, "limit": "2000", "offset": String(items.count)])
                    items.append(contentsOf: page.items)
                    total = page.total
                    if page.items.isEmpty { break }
                } while items.count < total
                return TxnPage(items: items, total: total)
            }
            if !Task.isCancelled { loadedMonth = selected }
        }
    }
}

/// One day's money: spending and income (transfers between your own accounts are neither).
struct DayTotals {
    var out = 0.0
    var inn = 0.0
    var lines: [Txn] = []

    static func byDay(_ txns: [Txn]) -> [Int: DayTotals] {
        var out: [Int: DayTotals] = [:]
        for t in txns where !t.isTransfer {
            let day = Int(t.date.suffix(2)) ?? 0
            let expense = NSDecimalNumber(decimal: t.categoryAmount("Expenses")).doubleValue
            let income = NSDecimalNumber(decimal: t.categoryAmount("Income")).doubleValue
            guard expense != 0 || income != 0 else { continue }
            var d = out[day] ?? DayTotals()
            d.out += expense
            d.inn += income
            d.lines.append(t)
            out[day] = d
        }
        return out
    }
}

struct MonthGrid: View {
    let pulse: Pulse
    var month: String? = nil
    private var calendarMonth: String { month ?? pulse.month ?? ReportingPeriod.month() }
    var days: [Int: DayTotals] = [:]
    @State private var picked: Int?

    /// Leading blanks before day 1 (Sunday-first weeks).
    private var leadingBlanks: Int {
        let parts = calendarMonth.split(separator: "-").compactMap { Int($0) }
        var comps = DateComponents()
        comps.year = parts.first
        comps.month = parts.count > 1 ? parts[1] : 1
        comps.day = 1
        let calendar = Calendar(identifier: .gregorian)
        guard let date = calendar.date(from: comps) else { return 0 }
        return calendar.component(.weekday, from: date) - 1
    }

    private var events: [Int: [PulseEvent]] {
        return Dictionary(grouping: pulse.upcoming.filter { $0.date.hasPrefix(calendarMonth) && $0.date >= (pulse.asOf ?? "") }) { Int($0.date.suffix(2)) ?? 0 }
    }

    private var weekdays: [String] {
        L10n.current == .zh
            ? ["日", "一", "二", "三", "四", "五", "六"]  // l10n-allow
            : ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    }

    var body: some View {
        let today = pulse.asOf?.hasPrefix(calendarMonth) == true ? Int((pulse.asOf ?? "").suffix(2)) ?? 0 : 0
        let usual = max(1, pulse.typicalMonth.double / Double(max(1, pulse.daysInMonth)))
        let byDay = events
        let count = Int(Day.monthBounds(calendarMonth).1.suffix(2)) ?? 30
        var cells: [Int?] = Array(repeating: nil, count: leadingBlanks) + (1...count).map { Optional($0) }
        while cells.count % 7 != 0 { cells.append(nil) }
        let columns = Array(repeating: GridItem(.flexible(), spacing: 1), count: 7)

        return VStack(alignment: .leading, spacing: 8) {
            LazyVGrid(columns: columns, spacing: 1) {
                ForEach(weekdays, id: \.self) { w in
                    Text(w).font(.ui(11.5)).foregroundStyle(Theme.muted)
                        .frame(maxWidth: .infinity).padding(.vertical, 7)
                        .background(Theme.fill)
                }
                ForEach(Array(cells.enumerated()), id: \.offset) { _, day in
                    if let day {
                        let totals = days[day]
                        Button { picked = day } label: {
                            DayCell(day: day, today: today, spent: totals?.out ?? 0, income: totals?.inn ?? 0,
                                    usual: usual, events: byDay[day] ?? [], lines: totals?.lines ?? [])
                        }
                            .buttonStyle(.plain)
                            .disabled((totals?.lines.isEmpty ?? true) && (byDay[day] ?? []).isEmpty)
                            .popover(isPresented: Binding(get: { picked == day }, set: { if !$0 { picked = nil } })) {
                                DayLines(day: day, month: calendarMonth, lines: totals?.lines ?? [], events: byDay[day] ?? [])
                            }
                    } else {
                        Theme.card.frame(height: 104)
                    }
                }
            }
            .background(Theme.border)
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Theme.border))
            Text(L("− 是支出，绿色 + 是收入，花得越多格子墨色越深；不含自己账户之间的转账。格子只预览主要商户；点一天查看完整交易、银行和账户。今天之后显示预计的账单和收入。"))
                .font(.ui(11.5)).foregroundStyle(Theme.muted)
        }
        .accessibilityLabel(L("本月日历"))
    }
}

private struct DayCell: View {
    let day: Int
    let today: Int
    let spent: Double
    var income: Double = 0
    let usual: Double
    let events: [PulseEvent]
    var lines: [Txn] = []

    /// An ink wash: the more spent, the darker the day, never so dark the figures lose contrast.
    private var heat: Double { spent > 0 ? 0.04 + min(1, spent / (usual * 3)) * 0.14 : 0 }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(verbatim: "\(day)").font(.mono(11)).foregroundStyle(today > 0 && day > today ? Theme.muted : Theme.ink)
            if abs(spent) > 0.005 {
                Text((spent < 0 ? "+" : "−") + m0(abs(spent))).font(.mono(10.5)).foregroundStyle(Theme.outflow)
                context("Expenses")
            }
            if abs(income) > 0.005 {
                Text((income < 0 ? "−" : "+") + m0(abs(income))).font(.mono(10.5)).foregroundStyle(Theme.inflow)
                context("Income")
            }
            ForEach(events.prefix(2)) { e in
                EventLine(event: e)
            }
        }
        .padding(7)
        .frame(maxWidth: .infinity, minHeight: 104, maxHeight: 104, alignment: .topLeading)
        .background(Theme.ink.opacity(heat))
        .background(Theme.card)
        .overlay {
            if day == today { Rectangle().strokeBorder(Theme.signal, lineWidth: 2) }
        }
    }

    @ViewBuilder private func context(_ category: String) -> some View {
        let matching = lines.filter { $0.categoryAmount(category) != 0 }
            .sorted { abs($0.categoryAmount(category)) > abs($1.categoryAmount(category)) }
        if let first = matching.first {
            let name = first.counterAccount == "Income:Salary" ? L("工资") : Format.tidy(first.payee ?? first.description)
            Text(name + (matching.count > 1 ? L(" 等 {n} 笔", ["n": matching.count]) : ""))
                .font(.ui(10)).foregroundStyle(Theme.muted).lineLimit(1)
        }
    }
}

private struct EventLine: View {
    let event: PulseEvent

    var body: some View {
        let outflow = event.amount.isNegative
        Text((event.kind == "card_payment" ? L("信用卡还款") : Format.tidy(eventName(event))) + " · " + L("预计"))
            .font(.ui(10.5)).lineLimit(1)
            .foregroundStyle(outflow ? Theme.muted : Theme.inflow)
    }
}

/// Projected cash: a step line over a faint fill, the low-balance floor as a dashed rule.
struct CashChart: View {
    let pulse: Pulse

    var body: some View {
        // The floor joins the scale only when the balance comes near it (otherwise the line flattens).
        let series = pulse.cashSeries.map(\.balance.double)
        let near = (series.min() ?? 0) - pulse.lowBalance.double < ((series.max() ?? 0) - (series.min() ?? 0)) * 2
        LineExhibitChart(
            xLabels: pulse.cashSeries.map { Format.day($0.date) },
            series: [ExhibitSeries(label: L("现金"), values: series)],
            rules: near ? [ExhibitRule(value: pulse.lowBalance.double, label: L("你设的最低余额 {x}", ["x": m0(pulse.lowBalance)]))] : [],
            format: { m0($0) },
            height: 220
        )
    }
}

/// The lines behind one day of the calendar: what, from which account, which category.
private struct DayLines: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let day: Int
    let month: String
    let lines: [Txn]
    var events: [PulseEvent] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(Format.day(String(format: "%@-%02d", month, day))).font(.display(16)).foregroundStyle(Theme.ink)
            if !events.isEmpty {
                Text(L("预计 · 尚未入账")).font(.ui(12, .medium)).foregroundStyle(Theme.muted)
                ForEach(events) { event in EventRow(event: event, showAccount: true) }
                if events.contains(where: { $0.kind == "card_payment" }) {
                    Text(L("还款日期根据历史记录推算。实际到期日和账单应还额请查看发卡行账单。"))
                        .font(.ui(11.5)).foregroundStyle(Theme.muted)
                }
                Text(L("根据历史账单和工资规律推算，实际金额与日期可能变化。"))
                    .font(.ui(11.5)).foregroundStyle(Theme.muted)
            }
            if !lines.isEmpty { ScrollView {
              VStack(spacing: 0) {
                ForEach(lines) { t in
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(Format.tidy(t.payee ?? t.description)).font(.ui(13)).foregroundStyle(Theme.ink)
                                .fixedSize(horizontal: false, vertical: true)
                            Text(Format.account(t.account) + " · " + (t.isSplit ? L("拆分") : Format.account(t.counterAccount)))
                                .font(.ui(11)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                            if t.description != Format.tidy(t.payee ?? t.description) {
                                Text(t.description).font(.ui(10.5)).foregroundStyle(Theme.muted)
                                    .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                            }
                        }
                        Spacer(minLength: 16)
                        Text(Format.money(t.amount.value, currency: t.commodity, digits: 2)).font(.mono(12.5))
                            .foregroundStyle(t.amount.isNegative ? Theme.ink : Theme.inflow)
                    }
                    .padding(.vertical, 7)
                    Hairline()
                }
              }
            }.frame(maxHeight: 360)
                Button(L("查看和修改这一天")) {
                    dismiss()
                    model.transactionDetail = TransactionDetail(month: String(format: "%@-%02d", month, day), cashflowOnly: true)
                }.buttonStyle(.keelOutlineCompact)
            }
        }
        .padding(16)
        .frame(width: 420)
    }
}
