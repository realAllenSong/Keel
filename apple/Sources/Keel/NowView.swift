import KeelCore
import SwiftUI

/// 现在 (DESIGN.md, 宣纸编辑部): the month in progress, about spending. One sentence for the month,
/// then 01 每日支出, a table of what was bought each day, and 02 支出分类 beside it.
struct NowView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("keel.netWorthCurrency") private var currency = "USD"
    @State private var flow = Loader<CashflowTable>()
    @State private var home = Loader<Home>("now.home")
    @State private var pulse = Loader<Pulse>("pulse")
    @State private var lines: [Txn] = []

    var body: some View {
        Page(width: 1232) {
            VStack(alignment: .leading, spacing: 24) {
                if let error = home.error ?? pulse.error { ErrorLine(message: L("读不到账本：{e}", ["e": error])) }
                if let h = home.value, !h.hasData {
                    FirstRun(home: h)
                } else if let p = pulse.value, p.hasData, let asOf = p.asOf {
                    content(p, asOf: asOf)
                } else if pulse.value?.hasData == false {
                    Text(L("这个月还没有交易。")).font(.ui(13)).foregroundStyle(Theme.muted)
                } else {
                    if let month = pulse.value?.month ?? model.currentMonth {
                        PageHead(title: Format.month(month), sub: currency) { MonthlyPeriodControls(month: month) }
                    }
                    Skeleton()
                }
            }
        }
        .task(id: model.revision) { await home.load { try await $0.home() } }
        .task(id: "\(model.revision)|\(currency)") { [currency] in
            // Fetch the month, its totals and its lines, then show them at once: switching the
            // currency changes the numbers in place (the old ones stay until the new ones are in).
            guard let api = model.api else { return }
            do {
                let p = try await api.pulse(currency: currency)
                var table: CashflowTable?
                var items: [Txn] = []
                if let month = p.month, let asOf = p.asOf {
                    table = try await api.cashflow(start: month, end: month, depth: 2, currency: currency, endDate: asOf)
                    items = (try? await api.transactions(month: month, currency: currency))?.items ?? []
                }
                try Task.checkCancellation()
                pulse.set(p)
                if let table { flow.set(table) }
                lines = items.filter { $0.kind == "normal" && ($0.counterAccount ?? "").hasPrefix("Expenses:") }
            } catch {
                pulse.fail(error)
            }
        }
    }

    @ViewBuilder private func content(_ p: Pulse, asOf: String) -> some View {
        let month = p.month ?? String(asOf.prefix(7))
        let missing = p.missingFx ?? []
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: 16) {
                Text(L("截至 {d}", ["d": Format.day(asOf)])).font(.ui(13)).foregroundStyle(Theme.muted)
                Spacer(minLength: 16)
                MonthlyPeriodControls(month: month)
            }
            if missing.isEmpty, lines.isEmpty, p.spent.double == 0, (income(for: month)?.double ?? 0) == 0 {
                emptyMonth(month)
            } else {
                Text(missing.isEmpty ? headline(p, month: month) : L("本月"))
                    .font(.serif(40)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 18)
                if missing.isEmpty, let note = note(p) {
                    Text(note).font(.ui(14)).foregroundStyle(Theme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 10)
                }
                if !missing.isEmpty {
                    ErrorLine(message: L("{c} 缺少汇率，完整收支总额暂不可用；明细仍可查看原币。", ["c": missing.joined(separator: ", ")]))
                        .padding(.top, 18)
                }
                Rectangle().fill(Theme.rule).frame(height: 1).padding(.top, 28)
                HStack(alignment: .top, spacing: 40) {
                    daily(month: month).frame(maxWidth: .infinity, alignment: .topLeading)
                    Rectangle().fill(Theme.rule).frame(width: 1)
                    categories(month: month).frame(width: 380, alignment: .topLeading)
                }
                .padding(.top, 24)
            }
        }
        .padding(.bottom, 24)
    }

    /// A month with nothing in it yet (the statements end earlier): say where they end and offer
    /// the way forward, instead of a page of zeros.
    @ViewBuilder private func emptyMonth(_ month: String) -> some View {
        let name = L10n.current == .zh ? L("{n} 月", ["n": Int(month.suffix(2)) ?? 0]) : Format.month(month, full: true)
        let last = model.lastDay
        let lastMonth = last.map { String($0.prefix(7)) }
        Text(L("{m}还没有记录", ["m": name])).font(.serif(40)).foregroundStyle(Theme.ink).padding(.top, 18)
        Text(last.map { L("你导入的账单最晚到 {d}。下载最新的账单拖进来，这个月就有了。", ["d": $0.count == 7 ? Format.month($0) : Format.day($0)]) }
            ?? L("下载最新的账单拖进来，这个月就有了。"))
            .font(.ui(14)).foregroundStyle(Theme.muted).padding(.top, 10)
        HStack(spacing: 12) {
            Button(L("拖入最新账单")) { model.chooseFiles() }.buttonStyle(.keelPrimary)
            if let lastMonth, lastMonth < month {
                Button(L("看 {m}", ["m": Format.month(lastMonth)]) + " →") { model.selectSummaryMonth(lastMonth) }
                    .buttonStyle(.keelOutline)
            }
        }
        .padding(.top, 20)
    }

    // MARK: - 01 每日支出

    private struct DayGroup: Identifiable {
        let date: String
        let items: [Txn]
        let total: Double
        var id: String { date }
    }

    /// Spending lines grouped by day, newest first; refunds count against their day.
    private var days: [DayGroup] {
        Dictionary(grouping: lines, by: \.date)
            .map { date, items in
                let sorted = items.sorted { spend($0) > spend($1) }
                return DayGroup(date: date, items: sorted, total: sorted.reduce(0) { $0 + spend($1) })
            }
            .filter { abs($0.total) >= 0.005 || !$0.items.isEmpty }
            .sorted { $0.date > $1.date }
    }

    /// What a line cost in the page's currency (positive = spent, negative = refunded).
    private func spend(_ t: Txn) -> Double {
        if let shown = t.reportDisplayAmount { return -shown.double }
        return -t.amount.double * (t.displayRate?.double ?? 1)
    }

    @ViewBuilder private func daily(month: String) -> some View {
        let groups = days
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .lastTextBaseline) {
                SectionTitle(number: 1, title: L("每日支出"))
                Spacer()
                Text(L("{n} 天有支出，共 {k} 笔", ["n": groups.count, "k": lines.count]))
                    .font(.ui(12.5)).foregroundStyle(Theme.muted)
            }
            .padding(.bottom, 14)
            if groups.isEmpty {
                Text(L("这个月还没有支出。")).font(.ui(13)).foregroundStyle(Theme.muted).padding(.vertical, 12)
            }
            ForEach(groups) { g in
                Rectangle().fill(Theme.ink).frame(height: 1)
                HStack(alignment: .top, spacing: 20) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(dayNumber(g.date)).font(.serif(22)).foregroundStyle(Theme.ink)
                        Text(weekday(g.date)).font(.ui(12)).foregroundStyle(Theme.muted)
                    }
                    .frame(width: 64, alignment: .leading)
                    .padding(.top, 10)
                    VStack(spacing: 0) {
                        ForEach(Array(g.items.enumerated()), id: \.element.id) { i, t in
                            if i > 0 { Rectangle().fill(Theme.border).frame(height: 1) }
                            row(t)
                        }
                    }
                    Text(m0(g.total, currency: shown)).font(.serif(17)).foregroundStyle(Theme.ink)
                        .frame(width: 104, alignment: .trailing)
                        .padding(.top, 12)
                }
                .padding(.bottom, 6)
            }
            if !groups.isEmpty { Rectangle().fill(Theme.ink).frame(height: 1) }
        }
    }

    private func row(_ t: Txn) -> some View {
        Button { model.transactionDetail = TransactionDetail(month: t.date, currency: shown) } label: {
            HStack(spacing: 14) {
                Text(merchant(t)).font(.ui(13.5)).foregroundStyle(Theme.ink).lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text(category(t)).font(.ui(12)).foregroundStyle(Theme.muted).lineLimit(1)
                    .frame(width: 110, alignment: .leading)
                Text(original(t)).font(.mono(13)).foregroundStyle(spend(t) < 0 ? Theme.inflow : Theme.ink2)
                    .frame(width: 96, alignment: .trailing)
            }
            .padding(.vertical, 9)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .pointingHandCursor()
    }

    private func merchant(_ t: Txn) -> String {
        let raw = (t.payee?.isEmpty == false ? t.payee! : t.description).trimmingCharacters(in: .whitespaces)
        return raw.isEmpty ? category(t) : raw
    }

    private func category(_ t: Txn) -> String {
        let parts = (t.counterAccount ?? "").split(separator: ":").dropFirst()
        return parts.map { Format.segment(String($0)) }.joined(separator: " · ")
    }

    /// The line in its own currency ("¥88", "$16.33"): a refund shows with a plus.
    private func original(_ t: Txn) -> String {
        let v = -t.amount.double
        let text = Format.money(Decimal(abs(v)), currency: t.commodity, digits: 2)
        return v < 0 ? "+" + text : text
    }

    private func dayNumber(_ iso: String) -> String {
        L("{d} 日", ["d": Int(iso.suffix(2)) ?? 0])
    }

    private func weekday(_ iso: String) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        guard let date = f.date(from: iso) else { return "" }
        f.locale = Locale(identifier: L10n.current == .zh ? "zh_CN" : "en_US")
        f.dateFormat = L10n.current == .zh ? "EEEE" : "EEE"
        return f.string(from: date)
    }

    // MARK: - 02 支出分类

    @ViewBuilder private func categories(month: String) -> some View {
        let rows = (flow.value?.expenses ?? [])
            .filter { $0.category.split(separator: ":").count == 2 && $0.total.double > 0.5 }
            .sorted { $0.total.double > $1.total.double }
        let maxValue = max(rows.first?.total.double ?? 1, 1)
        VStack(alignment: .leading, spacing: 0) {
            SectionTitle(number: 2, title: L("支出分类")).padding(.bottom, 14)
            ForEach(Array(rows.enumerated()), id: \.element.id) { i, row in
                Rectangle().fill(i == 0 ? Theme.ink : Theme.border).frame(height: 1)
                Button {
                    model.transactionDetail = TransactionDetail(month: month, category: row.category, cashflowOnly: true, currency: shown)
                } label: {
                    VStack(alignment: .leading, spacing: 7) {
                        HStack {
                            Text(Format.segment(String(row.category.split(separator: ":").last ?? "")))
                                .font(.ui(14)).foregroundStyle(Theme.ink)
                            Spacer()
                            Text(m0(row.total, currency: shown)).font(.mono(13.5)).foregroundStyle(Theme.ink)
                        }
                        GeometryReader { g in
                            ZStack(alignment: .leading) {
                                Capsule().fill(Theme.fill)
                                Capsule().fill(i == 0 ? Theme.signal : Theme.ink)
                                    .frame(width: max(3, g.size.width * row.total.double / maxValue))
                            }
                        }
                        .frame(height: 4)
                    }
                    .padding(.vertical, 11)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .pointingHandCursor()
            }
            if !rows.isEmpty { Rectangle().fill(Theme.ink).frame(height: 1) }
            Button(L("全部分类和收入") + " →") { model.route("/details?tab=spending&month=\(month)") }
                .buttonStyle(.plain).font(.ui(12.5)).foregroundStyle(Theme.ink2).pointingHandCursor()
                .padding(.top, 14)
        }
    }

    // MARK: - words

    /// "11 月到今天：收入 $1,900，支出 $13,020"
    private func headline(_ p: Pulse, month: String) -> String {
        let name = L10n.current == .zh ? L("{n} 月", ["n": Int(month.suffix(2)) ?? 0]) : Format.month(month, full: true)
        guard let income = income(for: month) else {
            return L("{m}到今天：已经花了 {e}", ["m": name, "e": amount(p.spent)])
        }
        return L("{m}到今天：收入 {i}，支出 {e}", ["m": name, "i": amount(income), "e": amount(p.spent)])
    }

    /// What drove the spending, when two lines are most of it.
    private func note(_ p: Pulse) -> String? {
        let spent = p.spent.double
        let largest = Array(lines.sorted { spend($0) > spend($1) }.prefix(2))
        let big = largest.reduce(0) { $0 + spend($1) }
        guard spent > 0, !largest.isEmpty, big >= spent * 0.4 else { return nil }
        let names = largest.map { L("{n} {x}", ["n": short($0), "x": amount(spend($0))]) }
        return L("{items}，是本月支出的大头。", ["items": names.joined(separator: L("和"))])
    }

    /// A short merchant name when there is one ("Atelier Boutique"), else the category ("房租").
    private func short(_ t: Txn) -> String {
        let name = merchant(t)
        if name.count <= 18 { return name }
        return Format.segment(String((t.counterAccount ?? "").split(separator: ":").last ?? ""))
    }

    private func income(for month: String) -> Money? {
        guard let table = flow.value, table.months == [month],
              (table.currency ?? "USD") == shown, (table.missingFx ?? []).isEmpty else { return nil }
        return table.incomeTotal.first
    }

    /// The currency of what is on screen (the picker may already say the next one).
    private var shown: String { pulse.value?.currency ?? currency }
    private func amount(_ value: Money) -> String { m0(value, currency: shown) }
    private func amount(_ value: Double) -> String { m0(value, currency: shown) }
}

/// "01 / 每日支出": a numbered exhibit title in the serif.
struct SectionTitle: View {
    let number: Int
    let title: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(String(format: "%02d /", number)).font(.serif(20, .regular)).foregroundStyle(Theme.muted)
            Text(title).font(.serif(22)).foregroundStyle(Theme.ink)
        }
    }
}

/// The page's sentence on the left, the month controls on the right.
struct PageHead<Trailing: View>: View {
    let title: String
    let sub: String
    @ViewBuilder var trailing: Trailing

    var body: some View {
        // The same head as 现在: the period line and its controls, then the sentence in the serif.
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .center, spacing: 16) {
                Text(sub).font(.ui(13)).foregroundStyle(Theme.muted)
                Spacer(minLength: 16)
                trailing
            }
            Text(title).font(.serif(40)).foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct Skeleton: View {
    var body: some View {
        VStack(spacing: 24) {
            RoundedRectangle(cornerRadius: 8).fill(Theme.fill.opacity(0.6)).frame(height: 520)
            RoundedRectangle(cornerRadius: 8).fill(Theme.fill.opacity(0.6)).frame(height: 300)
        }
    }
}
