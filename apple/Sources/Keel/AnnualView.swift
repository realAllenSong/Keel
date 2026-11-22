import KeelCore
import SwiftUI

/// The year uses the same statement vocabulary as each month, with an exact monthly table.
struct AnnualView: View {
    @Environment(AppModel.self) private var model
    @State private var data = Loader<AnnualReport>()
    @State private var netWorth = Loader<AnnualNetWorth>()
    @State private var assetMonth: String?
    @AppStorage("keel.netWorthCurrency") private var currency = "USD"

    private var through: String { ReportingPeriod.end(of: end, through: model.today) }

    private var months: [String] { ReportingPeriod.months(in: model.summaryYear, through: model.currentMonth ?? ReportingPeriod.month()) }
    private var start: String { max(months.first ?? "", model.firstMonth ?? "") }
    private var end: String { months.last ?? ReportingPeriod.month() }
    /// The period the net worth row belongs to (its currency is the report's: they load together).
    private var worthKey: String { "\(start)|\(end)|\(through)" }
    private var report: AnnualReport? { data.value.flatMap { $0.year == model.summaryYear && $0.start == start && $0.end == end && $0.through == through ? $0 : nil } }
    /// The currency of what is on screen (the picker may already say the next one).
    private var shown: String { report?.currency ?? currency }

    /// "2026 年到今天：收入 $84,391，支出 $83,163", the same kind of sentence as a month's.
    private var headline: String {
        let y = model.summaryYear
        guard let r = report, (r.flow.missingFx ?? []).isEmpty else { return L("{y} 年", ["y": y]) }
        let t = r.flow.totals
        let current = y == Int((model.currentMonth ?? ReportingPeriod.month()).prefix(4))
        let values: [String: CustomStringConvertible] = [
            "y": y, "i": Format.money(t.income, currency: shown, digits: 0), "e": Format.money(t.expenses, currency: shown, digits: 0),
        ]
        return current ? L("{y} 年到今天：收入 {i}，支出 {e}", values) : L("{y} 年：收入 {i}，支出 {e}", values)
    }

    var body: some View {
        Page(width: 1232) {
            VStack(alignment: .leading, spacing: 24) {
                PageHead(title: headline,
                         sub: L("{a} 到 {b} · 已入账收支 · {c}", ["a": start + "-01", "b": through, "c": shown])) {
                    controls
                }
                if let r = report {
                    let totals = r.flow.totals
                    let missing = r.flow.missingFx ?? []
                    Figs(items: [
                        .init(label: L("收入"), value: money(totals.income, complete: missing.isEmpty), action: { yearDetail(category: "Income") }),
                        .init(label: L("支出"), value: money(totals.expenses, complete: missing.isEmpty), action: { yearDetail(category: "Expenses") }),
                        .init(label: L("收支结余"), value: money(totals.savings, complete: missing.isEmpty),
                              sub: missing.isEmpty ? totals.rate.map { L("储蓄率 {p}", ["p": Format.percent($0)]) } : nil, accent: true, action: { yearDetail(category: nil) }),
                    ])
                    if !missing.isEmpty {
                        Text(L("{c} 缺少汇率，完整收支总额暂不可用；明细仍可查看原币。", ["c": missing.joined(separator: ", ")])).font(.ui(12)).foregroundStyle(Theme.signalInk)
                    }
                    if let original = r.flow.originalFlows, original.contains(where: { $0.currency != shown }) {
                        originalFlowBreakdown(original)
                    }
                    HStack(alignment: .top, spacing: 24) {
                        monthlyTable(r.flow).frame(maxWidth: .infinity, alignment: .topLeading).flatSection()
                        MonthlyAssets(month: end)
                    }
                } else if let error = data.error {
                    ErrorLine(message: error)
                } else { Skeleton() }
            }
        }
        .task(id: "\(model.revision)|\(model.summaryYear)|\(start)|\(end)|\(through)|\(currency)") {
            // The table and the net-worth row load together and swap at once: switching the
            // currency changes the numbers in place instead of collapsing while they reload.
            guard start <= end, let api = model.api else { return }
            let (year, start, end, through, currency, worthKey, today) = (model.summaryYear, start, end, through, currency, worthKey, model.today)
            do {
                async let flow = api.cashflow(start: start, end: end, depth: 2, currency: currency, endDate: through)
                async let points = api.networth(start: start, end: end, currency: currency)
                var worth = Dictionary(uniqueKeysWithValues: try await points.filter { ($0.missingFx ?? []).isEmpty }.map { ($0.month, $0.netWorth) })
                if end == String(today.prefix(7)) {
                    let current = try await api.balanceSheet(date: today, currency: currency)
                    worth[end] = (current.missingFx ?? []).isEmpty ? current.netWorth : nil
                }
                let table = try await flow
                try Task.checkCancellation()
                data.set(AnnualReport(year: year, start: start, end: end, through: through, currency: currency, flow: table))
                netWorth.set(AnnualNetWorth(key: worthKey, months: worth))
            } catch {
                data.fail(error)
            }
        }
        .sheet(isPresented: Binding(get: { assetMonth != nil }, set: { if !$0 { assetMonth = nil } })) {
            if let month = assetMonth { AssetDetailsSheet(month: month).environment(model) }
        }
    }

    private var controls: some View {
        HStack(spacing: 12) {
            Segmented(items: [(false, L("月")), (true, L("年"))], selection: Binding(
                get: { model.showAnnual },
                set: { value in
                    if !value {
                        let remembered = model.reportMonth ?? model.currentMonth ?? end
                        model.selectSummaryMonth(remembered.hasPrefix(String(model.summaryYear)) ? remembered : end)
                    }
                }
            ), fill: true)
            ReportCurrencyPicker(currency: $currency)
            HStack(spacing: 8) {
                Button(L("上一年")) { model.summaryYear -= 1 }.buttonStyle(.keelOutline)
                    .disabled(model.summaryYear <= (Int((model.firstMonth ?? end).prefix(4)) ?? model.summaryYear))
                Text(String(model.summaryYear)).font(.mono(13)).foregroundStyle(Theme.ink)
                Button(L("下一年")) { model.summaryYear += 1 }.buttonStyle(.keelOutline)
                    .disabled(model.summaryYear >= (Int((model.currentMonth ?? end).prefix(4)) ?? model.summaryYear))
            }
        }
    }

    private func yearDetail(category: String?) {
        model.transactionDetail = TransactionDetail(month: String(model.summaryYear), category: category, cashflowOnly: true, through: through, currency: shown)
    }

    private func originalFlowBreakdown(_ flows: [OriginalFlow]) -> some View {
        DisclosureGroup(L("按原币查看收支")) {
            Grid(alignment: .trailing, horizontalSpacing: 28, verticalSpacing: 12) {
                GridRow {
                    Text(L("币种")).gridColumnAlignment(.leading)
                    Text(L("收入"))
                    Text(L("支出"))
                    Text(L("结余"))
                }.font(.ui(12)).foregroundStyle(Theme.muted)
                ForEach(flows) { flow in
                    GridRow {
                        Text(flow.currency)
                        Text(Format.money(flow.income, currency: flow.currency, digits: 2))
                        Text(Format.money(flow.expenses, currency: flow.currency, digits: 2))
                        Text(Format.money(flow.savings, currency: flow.currency, digits: 2))
                    }.font(.mono(13)).foregroundStyle(Theme.ink)
                }
            }.padding(.vertical, 12)
            Text(L("上方总览统一折算为所选币种。钱包账单提供流水，余额来自对应账户记录。"))
                .font(.ui(11.5)).foregroundStyle(Theme.muted)
        }.font(.ui(13)).foregroundStyle(Theme.muted)
    }

    private func monthlyTable(_ flow: CashflowTable) -> some View {
        let worth = netWorth.value.flatMap { $0.key == worthKey ? $0.months : nil } ?? [:]
        return VStack(alignment: .leading, spacing: 0) {
            Text(L("每月收支")).font(.display(21)).foregroundStyle(Theme.ink).padding(.bottom, 14)
            Grid(alignment: .trailing, horizontalSpacing: 12, verticalSpacing: 0) {
            row([L("月份"), L("收入"), L("支出"), L("结余"), L("净资产")], heading: true)
            Hairline(color: Theme.rule).gridCellColumns(5)
            ForEach((1...12).map { String(format: "%04d-%02d", model.summaryYear, $0) }, id: \.self) { month in
                if let i = flow.months.firstIndex(of: month) {
                    GridRow {
                        Button(Format.month(month, short: true) + (month == model.currentMonth ? " · " + L("进行中") : "")) {
                            model.selectSummaryMonth(month)
                        }.buttonStyle(.plain).font(.ui(12.5)).foregroundStyle(Theme.ink).frame(width: 108, alignment: .leading)
                        amount(flow.incomeTotal[i], month: month, category: "Income", complete: (flow.missingFx ?? []).isEmpty)
                        amount(flow.expensesTotal[i], month: month, category: "Expenses", complete: (flow.missingFx ?? []).isEmpty)
                        amount(flow.savings[i], month: month, category: nil, complete: (flow.missingFx ?? []).isEmpty)
                        if let amount = worth[month] {
                            Button(Format.money(amount, currency: shown, digits: 2)) { assetMonth = month }
                                .buttonStyle(.plain).font(.mono(12)).foregroundStyle(Theme.ink)
                                .frame(maxWidth: .infinity, alignment: .trailing)
                        } else { Text("—").foregroundStyle(Theme.muted) }
                    }.padding(.vertical, 11)
                } else {
                    row([Format.month(month, short: true), "—", "—", month > end ? L("尚未发生") : L("未覆盖"), "—"])
                }
                Hairline(color: Theme.border.opacity(0.6)).gridCellColumns(5)
            }
            }
            Text(L("点月份看当月，点金额看每笔；进行中的月份不作为完整月计算平均。"))
                .font(.ui(11.5)).foregroundStyle(Theme.muted).padding(.top, 14)
            Text(L("收支结余是本期收入减支出；资产区显示账户余额。信用卡消费在入账时计支出，之后还款本金不再计一次。"))
                .font(.ui(11.5)).foregroundStyle(Theme.muted).padding(.top, 6)
            Text(L("净资产为当时资产减欠款：历史月份取月末，本月截至今天。点净资产看当时的账户。"))
                .font(.ui(11.5)).foregroundStyle(Theme.muted).padding(.top, 6)
        }
    }

    private func row(_ values: [String], heading: Bool = false) -> some View {
        GridRow {
            ForEach(Array(values.enumerated()), id: \.offset) { i, value in
                Text(value).font(.ui(heading ? 11 : 12)).foregroundStyle(Theme.muted)
                    .frame(width: i == 0 ? 108 : nil, alignment: .leading)
                    .frame(maxWidth: i == 0 ? nil : .infinity, alignment: .trailing)
            }
        }.padding(.vertical, 10)
    }

    private func money(_ value: Decimal, complete: Bool) -> String {
        complete ? Format.money(value, currency: shown, digits: 2) : "—"
    }

    private func amount(_ value: Money, month: String, category: String?, complete: Bool) -> some View {
        Button(money(value.value, complete: complete)) { model.transactionDetail = TransactionDetail(month: month, category: category, cashflowOnly: true, through: ReportingPeriod.end(of: month, through: model.today), currency: shown) }
            .buttonStyle(.plain).font(.mono(12)).foregroundStyle(Theme.ink)
            .frame(maxWidth: .infinity, alignment: .trailing)
    }
}

private struct AnnualReport {
    let year: Int
    let start: String
    let end: String
    let through: String
    let currency: String
    let flow: CashflowTable
}

private struct AnnualNetWorth {
    let key: String
    let months: [String: Money]
}
