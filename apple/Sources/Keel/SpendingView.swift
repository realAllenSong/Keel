import KeelCore
import SwiftUI

/// Income and spending for the same selected month, with category drilldowns on both sides.
struct SpendingView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("keel.netWorthCurrency") private var reportCurrency = "USD"
    private var currency: String { embedded ? reportCurrency : "USD" }
    var selectedMonth: String? = nil
    var embedded = false
    private var periodMonth: String? { selectedMonth ?? model.month }
    private var through: String? { periodMonth.map { ReportingPeriod.end(of: $0, through: model.today) } }
    @State private var flows = Loader<MonthlyBreakdown>("cashflow.breakdown")
    @State private var insights = Loader<[InsightOut]>("spending.insights")
    @State private var open: Set<String> = []
    @State private var showTable = false

    var body: some View {
        // Not gated on the selected currency: while a switch reloads, the last numbers stay (in
        // their own currency) and change in place, instead of the section collapsing.
        let data = flows.value.flatMap { $0.month == periodMonth && $0.through == through ? $0 : nil }
        let shown = data?.currency ?? currency
        VStack(alignment: .leading, spacing: 40) {
            if !embedded { VStack(alignment: .leading, spacing: 10) {
                Text(L("这个月，进来多少、花了多少？")).font(.display(30)).foregroundStyle(Theme.ink)
                Text(L("收入和支出按所选月份统计，自己账户之间的转账不计入。"))
                    .font(.ui(13)).foregroundStyle(Theme.muted)
            }
            }
            if let error = flows.error { ErrorLine(message: L("收支暂时读不到：{e}", ["e": error])) }
            if let data {
                if !embedded { Figs(items: [
                    .init(label: L("收入"), value: Format.money(data.incomeTotal, currency: shown, digits: 2)),
                    .init(label: L("支出"), value: Format.money(data.spendingTotal, currency: shown, digits: 2)),
                    .init(label: L("结余"), value: Format.money(data.incomeTotal - data.spendingTotal, currency: shown, digits: 2)),
                ]) }
                Text(periodMonth == model.currentMonth ? L("点收入来源或支出类别，查看和修改每一笔。") : L("条是本月金额，竖线是过去 12 个月的平均。点收入来源或支出类别看每一笔。"))
                    .font(.ui(13)).foregroundStyle(Theme.muted)
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .top, spacing: 40) {
                        categorySection(L("支出类别"), nodes: data.spending, empty: L("这个月还没有支出。"), currency: shown).frame(minWidth: 360)
                        categorySection(L("收入来源"), nodes: data.income, empty: L("这个月还没有收入。"), currency: shown).frame(minWidth: 360)
                    }
                    VStack(alignment: .leading, spacing: 32) {
                        categorySection(L("支出类别"), nodes: data.spending, empty: L("这个月还没有支出。"), currency: shown)
                        categorySection(L("收入来源"), nodes: data.income, empty: L("这个月还没有收入。"), currency: shown)
                    }
                }
            } else if flows.error == nil {
                SkeletonRows()
            }
            if !embedded, let items = insights.value, !items.isEmpty {
                VStack(alignment: .leading, spacing: 0) {
                    Text(L("有几处变化，值得看看")).font(.display(21)).foregroundStyle(Theme.ink).padding(.bottom, 12)
                    ForEach(Array(items.enumerated()), id: \.offset) { i, item in
                        if i > 0 { Hairline() }
                        InsightRow(insight: item)
                    }
                }
                .frame(maxWidth: 760, alignment: .leading)
            }
            if !embedded { VStack(alignment: .leading, spacing: 20) {
                Hairline()
                Button { withAnimation(.easeOut(duration: 0.15)) { showTable.toggle() } } label: {
                    HStack(spacing: 6) {
                        Image(systemName: showTable ? "chevron.down" : "chevron.right").font(.ui(10, .semibold))
                        Text(L("收支明细表")).font(.ui(13))
                    }
                    .foregroundStyle(Theme.muted)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                if showTable { CashflowTableView() }
            } }
        }
        .task(id: "\(model.revision)|\(through ?? "")|\(currency)") { [currency, through] in
            guard let month = periodMonth else { return }
            async let a: Void = flows.load { api in
                async let spending = api.expenses(month: month, currency: currency, endDate: through)
                async let income = api.income(month: month, currency: currency, endDate: through)
                return try await MonthlyBreakdown(month: month, currency: currency, through: through, spending: spending, income: income)
            }
            async let b: Void = loadInsights(month)
            _ = await (a, b)
        }
    }

    private func loadInsights(_ month: String) async {
        if !embedded { await insights.load { try await $0.insights(month: month) } }
    }

    private func categorySection(_ title: String, nodes: [ExpenseNode], empty: String, currency: String) -> some View {
        let compare = periodMonth != model.currentMonth
        let top = nodes.filter { $0.depth == 2 && (!embedded || $0.amount.value != 0) }.sorted { $0.amount.double > $1.amount.double }
        let maxValue = max(1, top.flatMap { compare ? [$0.amount.double, $0.avg12.double] : [abs($0.amount.double)] }.max() ?? 1)
        return VStack(alignment: .leading, spacing: 0) {
            Text(title).font(.display(21)).foregroundStyle(Theme.ink).padding(.bottom, 12)
            if top.isEmpty { Text(empty).font(.ui(13)).foregroundStyle(Theme.muted).padding(.vertical, 16) }
            ForEach(top) { n in
                CategoryRow(month: periodMonth, currency: currency, through: through, showComparison: compare, node: n, level: 0, maxValue: maxValue, expandable: !children(of: n, in: nodes).isEmpty, open: open.contains(n.category)) {
                    if open.contains(n.category) { open.remove(n.category) } else { open.insert(n.category) }
                }
                if open.contains(n.category) {
                    ForEach(children(of: n, in: nodes)) { c in
                        CategoryRow(month: periodMonth, currency: currency, through: through, showComparison: compare, node: c, level: 1, maxValue: maxValue, expandable: false, open: false) {}
                    }
                }
            }
        }.frame(maxWidth: .infinity, alignment: .topLeading)
    }

    private func children(of n: ExpenseNode, in nodes: [ExpenseNode]) -> [ExpenseNode] {
        nodes.filter { $0.depth == n.depth + 1 && $0.category.hasPrefix(n.category + ":") }
            .sorted { $0.amount.double > $1.amount.double }
    }
}

private struct MonthlyBreakdown {
    let month: String
    let currency: String
    let through: String?
    let spending: [ExpenseNode]
    let income: [ExpenseNode]
    var incomeTotal: Decimal { income.filter { $0.depth == 2 }.reduce(Decimal.zero) { $0 + $1.amount.value } }
    var spendingTotal: Decimal { spending.filter { $0.depth == 2 }.reduce(Decimal.zero) { $0 + $1.amount.value } }
}

private struct CategoryRow: View {
    @Environment(AppModel.self) private var model
    let month: String?
    let currency: String
    let through: String?
    var showComparison = true
    let node: ExpenseNode
    let level: Int
    let maxValue: Double
    let expandable: Bool
    let open: Bool
    let toggle: () -> Void
    @State private var hover = false

    var body: some View {
        let amount = node.amount.double
        let avg = node.avg12.double
        let deviation = avg > 0 ? (amount - avg) / avg : nil
        let name = Format.segment(String(node.category.split(separator: ":").last ?? ""))
        VStack(spacing: 0) {
            HStack(alignment: .center, spacing: 20) {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        if expandable {
                            Button(action: toggle) {
                                Image(systemName: open ? "chevron.down" : "chevron.right")
                                    .font(.ui(10, .semibold)).foregroundStyle(Theme.muted)
                                    .frame(width: 20, height: 20).contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .help(open ? L("收起 {name}", ["name": name]) : L("展开 {name}", ["name": name]))
                        } else {
                            Color.clear.frame(width: 20, height: 20)
                        }
                        Button {
                            if let month { model.transactionDetail = TransactionDetail(month: month, category: node.category, cashflowOnly: true, through: through, currency: currency) }
                        } label: {
                            Text(name).font(.ui(level == 0 ? 14 : 13)).foregroundStyle(Theme.ink)
                                .underline(hover)
                        }
                        .buttonStyle(.plain)
                        .onHover { hover = $0 }
                    }
                    if showComparison, level == 0, let d = deviation, abs(d) > 0.25 {
                        Text(node.category.hasPrefix("Income:")
                             ? (d > 0 ? L("比平时多收入 {x}。", ["x": m0(abs(amount - avg), currency: currency)]) : L("比平时少收入 {x}。", ["x": m0(abs(amount - avg), currency: currency)]))
                             : (d > 0 ? L("比平时多花了 {x}。", ["x": m0(abs(amount - avg), currency: currency)]) : L("比平时少花了 {x}。", ["x": m0(abs(amount - avg), currency: currency)])))
                            .font(.ui(11.5)).foregroundStyle(Theme.muted).padding(.leading, 28)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.leading, CGFloat(level) * 24)
                GeometryReader { g in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Theme.fill).frame(height: 4)
                        Capsule().fill(Theme.ink.opacity(0.55)).frame(width: max(2, g.size.width * amount / maxValue), height: 4)
                        if showComparison { Rectangle().fill(Theme.ink).frame(width: 1, height: 12).offset(x: g.size.width * avg / maxValue) }
                    }
                    .frame(maxHeight: .infinity)
                }
                .frame(height: 12)
                .frame(maxWidth: .infinity)
                .accessibilityLabel(showComparison ? L("本月 {a}，过去 12 个月平均 {b}", ["a": m0(amount, currency: currency), "b": m0(avg, currency: currency)]) : L("已入账 {a}", ["a": Format.money(node.amount, currency: currency, digits: 2)]))
                Button {
                    if let month { model.transactionDetail = TransactionDetail(month: month, category: node.category, cashflowOnly: true, through: through, currency: currency) }
                } label: {
                    VStack(alignment: .trailing, spacing: 4) {
                        Text(Format.money(node.amount, currency: currency, digits: 2)).font(.mono(13.5)).foregroundStyle(Theme.ink)
                        if showComparison { Text(L("平时 {x}", ["x": m0(node.avg12, currency: currency)])).font(.ui(11.5)).foregroundStyle(Theme.muted) }
                    }
                    .frame(minWidth: 96, alignment: .trailing)
                }.buttonStyle(.plain)
            }
            .padding(.vertical, 16)
            Hairline(color: Theme.border.opacity(0.6))
        }
    }
}

private struct InsightRow: View {
    @Environment(AppModel.self) private var model
    let insight: InsightOut
    @State private var hover = false

    var body: some View {
        Button {
            let f = insight.filters
            if let month = f["month"] ?? model.month {
                model.transactionDetail = TransactionDetail(month: month, category: f["category"], account: f["account"])
            }
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                Text(insight.title + " →").font(.ui(13.5)).foregroundStyle(Theme.ink)
                Text(insight.body).font(.ui(13)).foregroundStyle(Theme.muted).lineSpacing(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.vertical, 14)
            .padding(.horizontal, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(hover ? Theme.fill.opacity(0.4) : .clear)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
    }
}

struct SkeletonRows: View {
    var count = 5
    var body: some View {
        VStack(spacing: 12) {
            ForEach(0..<count, id: \.self) { _ in
                RoundedRectangle(cornerRadius: 6).fill(Theme.fill.opacity(0.6)).frame(height: 40)
            }
        }
    }
}

/// Twelve months to the report month: income and expense categories, totals, savings and rate.
/// Every amount opens 交易 for that category and month.
private struct CashflowTableView: View {
    @Environment(AppModel.self) private var model
    @State private var data = Loader<CashflowTable>("spending.table")
    @State private var depth = 2

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(L("收支明细表")).font(.display(21)).foregroundStyle(Theme.ink)
                Spacer()
                Segmented(items: [(2, L("大类")), (3, L("细类"))], selection: $depth, fill: true)
            }
            Text(L("截至所选月份的 12 个月 · 单位美元 · 内部转账与估值变动不计入 · 点击数字查看明细"))
                .font(.ui(11.5)).foregroundStyle(Theme.muted)
            if let t = data.value {
                ScrollView(.horizontal, showsIndicators: false) { table(t).padding(.top, 12) }
            } else if let e = data.error {
                ErrorLine(message: e)
            } else {
                SkeletonRows()
            }
        }
        .task(id: "\(model.revision)|\(model.month ?? "")|\(depth)") {
            guard let month = model.month else { return }
            await data.load { [depth] in try await $0.cashflow(start: AppModel.shift(month, -11), end: month, depth: depth) }
        }
    }

    private let labelWidth: CGFloat = 170
    private let cellWidth: CGFloat = 66

    private func table(_ t: CashflowTable) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 0) {
                Text(L("类别").uppercased()).font(.ui(10.5)).tracking(1.2).frame(width: labelWidth, alignment: .leading)
                ForEach(t.months, id: \.self) { m in
                    Text(String(m.dropFirst(2)).replacingOccurrences(of: "-", with: "/")).font(.mono(10.5)).frame(width: cellWidth, alignment: .trailing)
                }
                Text(L("合计").uppercased()).font(.ui(10.5)).tracking(1.2).frame(width: cellWidth + 14, alignment: .trailing)
            }
            .foregroundStyle(Theme.muted)
            .padding(.vertical, 8)
            Hairline(color: Theme.rule)
            section(L("收入"), rows: t.income, months: t.months)
            totalRow(L("收入合计"), values: t.incomeTotal)
            section(L("支出"), rows: t.expenses, months: t.months)
            totalRow(L("支出合计"), values: t.expensesTotal)
            Rectangle().fill(Theme.rule).frame(height: 2).padding(.top, 6)
            HStack(spacing: 0) {
                Text(L("储蓄")).font(.ui(13, .medium)).frame(width: labelWidth, alignment: .leading)
                ForEach(Array(t.savings.enumerated()), id: \.offset) { _, v in
                    Text(n0(v.double)).font(.mono(12, .medium)).foregroundStyle(v.isNegative ? Theme.outflow : Theme.ink)
                        .frame(width: cellWidth, alignment: .trailing)
                }
                Text(n0(t.savings.reduce(0) { $0 + $1.double })).font(.mono(12, .medium)).frame(width: cellWidth + 14, alignment: .trailing)
            }
            .foregroundStyle(Theme.ink)
            .padding(.vertical, 10)
            HStack(spacing: 0) {
                Text(L("储蓄率")).font(.ui(13)).frame(width: labelWidth, alignment: .leading)
                ForEach(Array(t.savingsRate.enumerated()), id: \.offset) { _, r in
                    Text(r.map { Format.percent($0) } ?? "—").font(.mono(12)).frame(width: cellWidth, alignment: .trailing)
                }
            }
            .foregroundStyle(Theme.muted)
            .padding(.vertical, 4)
        }
    }

    private func section(_ title: String, rows: [CashflowRow], months: [String]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(title).font(.display(16)).foregroundStyle(Theme.ink).padding(.top, 18).padding(.bottom, 4)
            ForEach(rows) { r in
                HStack(spacing: 0) {
                    Text(Format.account(r.category)).font(.ui(13)).foregroundStyle(Theme.ink).lineLimit(1)
                        .frame(width: labelWidth, alignment: .leading)
                    ForEach(Array(r.values.enumerated()), id: \.offset) { i, v in
                        Group {
                            if v.double == 0 {
                                Text("·").foregroundStyle(Theme.muted.opacity(0.4))
                            } else {
                                Button(n0(v.double)) { model.transactionDetail = TransactionDetail(month: months[i], category: r.category) }
                                    .buttonStyle(.plain)
                                    .foregroundStyle(Theme.ink)
                            }
                        }
                        .font(.mono(12))
                        .frame(width: cellWidth, alignment: .trailing)
                    }
                    Text(n0(r.total.double)).font(.mono(12)).foregroundStyle(Theme.muted).frame(width: cellWidth + 14, alignment: .trailing)
                }
                .padding(.vertical, 6)
                Hairline(color: Theme.border.opacity(0.5))
            }
        }
    }

    private func totalRow(_ label: String, values: [Money]) -> some View {
        HStack(spacing: 0) {
            Text(label).font(.ui(13, .medium)).frame(width: labelWidth, alignment: .leading)
            ForEach(Array(values.enumerated()), id: \.offset) { _, v in
                Text(n0(v.double)).font(.mono(12, .medium)).frame(width: cellWidth, alignment: .trailing)
            }
            Text(n0(values.reduce(0) { $0 + $1.double })).font(.mono(12, .medium)).frame(width: cellWidth + 14, alignment: .trailing)
        }
        .foregroundStyle(Theme.ink)
        .padding(.vertical, 8)
    }

    /// "4,316" (no symbol: the table says it is in dollars).
    private func n0(_ v: Double) -> String {
        let s = m0(v)
        return s.replacingOccurrences(of: "$", with: "")
    }
}
