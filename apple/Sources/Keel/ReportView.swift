import KeelCore
import SwiftUI

/// 月报: one closed month. Its sentence, five figures and its own cash timeline, then four
/// exhibits: twelve months of income and spending, categories against usual, net worth by class
/// and the lighthouses. Steps back through earlier months; stepping past the newest opens 本月.
struct ReportView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("keel.netWorthCurrency") private var currency = "USD"
    @State private var home = Loader<Home>("report.home")
    @State private var overview = Loader<Overview>("report.overview")
    @State private var networth = Loader<[NWPoint]>("report.networth")
    @State private var cash = Loader<(month: String, currency: String, days: [CashFlowDay])>()
    static let side: CGFloat = 480
    private var reportMonth: String? { model.reportMonth ?? model.latestMonth }
    private var report: Overview? {
        overview.value.flatMap { $0.month == reportMonth ? $0 : nil }
    }
    /// The currency of what is on screen (the picker may already say the next one).
    private var shown: String { report?.currency ?? currency }
    private var worthPoints: [NWPoint] {
        guard let points = networth.value, let last = points.last, last.month == reportMonth,
              (last.currency ?? "USD") == shown else { return [] }
        return points
    }

    var body: some View {
        Page(width: 1232) {
            VStack(alignment: .leading, spacing: 24) {
                if let error = home.error ?? overview.error ?? cash.error { ErrorLine(message: L("读不到账本：{e}", ["e": error])) }
                if let h = home.value, !h.hasData {
                    FirstRun(home: h)
                } else if let report {
                    content(report)
                } else {
                    if let month = reportMonth {
                        PageHead(title: Format.month(month), sub: currency) { stepper(month) }
                    }
                    Skeleton()
                }
            }
        }
        .task(id: model.revision) { await home.load { try await $0.home() } }
        .task(id: "\(model.revision)|\(reportMonth ?? "")|\(currency)") { [currency] in
            guard let month = reportMonth else { return }
            let (first, last) = Day.monthBounds(month)
            // All three, then one swap: a currency switch updates the page in place.
            guard let api = model.api else { return }
            do {
                async let o = api.overview(month: month, currency: currency)
                async let n = api.networth(end: month, currency: currency)
                // One day before the 1st: its closing balance is the month's opening balance.
                async let c = api.cashline(start: Day.add(first, -1), end: last, currency: currency)
                let (ov, nw, cl) = try await (o, n, c)
                try Task.checkCancellation()
                overview.set(ov)
                networth.set(nw)
                cash.set((month, currency, cl))
            } catch {
                overview.fail(error)
            }
        }
    }

    @ViewBuilder private func content(_ report: Overview) -> some View {
        let s = report.current
        let missing = report.missingFx ?? []
        let bounds = Day.monthBounds(s.month)
        PageHead(title: missing.isEmpty ? headline(s) : Format.month(s.month), sub: L("{a} 到 {b}，按这个月已入账的交易汇总。", ["a": Format.day(bounds.0), "b": Format.day(bounds.1)])) {
            stepper(s.month)
        }
        if missing.isEmpty {
            statement(s, first: bounds.0, last: bounds.1)
        } else {
            ErrorLine(message: L("{c} 缺少汇率，完整收支总额暂不可用；明细仍可查看原币。", ["c": missing.joined(separator: ", ")]))
        }
        SpendingView(selectedMonth: s.month, embedded: true)
        HStack(alignment: .top, spacing: 24) {
            if missing.isEmpty {
                Exhibit(number: 2, title: monthsTitle, sub: L("每月收入和支出 · {c} · 下方是储蓄率。最右一组是 {m}。", ["m": Format.month(s.month, short: true), "c": shown])) {
                    MonthsChart(series: report.series, onPickMonth: { model.selectSummaryMonth($0) }, currency: shown)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading).flatSection()
            }
            MonthlyAssets(month: s.month)
        }
        .fixedSize(horizontal: false, vertical: true)
        Exhibit(number: 3, title: networthTitle, sub: L("按月末汇率折算的净资产。") + " · " + shown) {
            let missing = Set(worthPoints.flatMap { $0.missingFx ?? [] }).sorted()
            if missing.isEmpty {
                NetWorthExhibitChart(series: worthPoints, currency: shown)
            } else {
                AssetMissingRates(pairs: missing)
            }
        }
        .flatSection()
    }

    // MARK: the month

    private func stepper(_ month: String) -> some View { MonthlyPeriodControls(month: month) }

    private func headline(_ s: MonthSummary) -> String {
        let name = L10n.current == .zh ? L("{n} 月", ["n": Int(s.month.suffix(2)) ?? 0]) : Format.month(s.month, full: true)
        var text = L("{m}，你挣了 {i}，花了 {e}", ["m": name, "i": amount(s.income), "e": amount(s.expenses)])
        if let rate = s.savingsRate, s.savings.double > 0 {
            text += L("，存下 {p}。", ["p": Format.percent(rate)])
        } else {
            text += L("，入不敷出。")
        }
        return text
    }

    private func statement(_ s: MonthSummary, first: String, last: String) -> some View {
        let prev = report?.previous
        let all = cash.value.flatMap { $0.month == s.month && $0.currency == shown ? $0.days : nil } ?? []
        let days = all.filter { $0.date >= first }
        let open = all.first { $0.date < first }?.balance.double
        let close = days.last?.balance.double
        let prevName = prev.map { Format.month($0.month, short: true) } ?? ""
        let expChange = prev.map { $0.expenses.double > 0 ? (s.expenses.double - $0.expenses.double) / $0.expenses.double : 0 }
        return VStack(alignment: .leading, spacing: 28) {
            Figs(items: [
                .init(label: L("收入"), value: amount(s.income), sub: prev.map { prevName + " " + amount($0.income) }),
                .init(label: L("支出"), value: amount(s.expenses), sub: expChange.map { c in
                    abs(c) < 0.01 ? L("和{m}持平", ["m": prevName])
                        : c > 0 ? L("比{m}多 {p}", ["m": prevName, "p": Format.percent(c)]) : L("比{m}少 {p}", ["m": prevName, "p": Format.percent(-c)])
                }),
                .init(
                    label: L("存下"), value: amount(s.savings),
                    sub: s.savingsRate.map { r in
                        L("储蓄率 {p}", ["p": Format.percent(r)]) + (prev?.savingsRate.map { L("，{m} {p}", ["m": prevName, "p": Format.percent($0)]) } ?? "")
                    },
                    accent: true
                ),
                .init(label: L("现金账户"), value: close.map(amount) ?? "—", sub: open.map { L("月初 {x}", ["x": amount($0)]) }),
            ])
            if !days.isEmpty {
                Exhibit(
                    number: 1, title: cashTitle(s, days: days, open: open, close: close),
                    sub: L("现金账户余额 · {c} · 下方是每天的进出。", ["c": shown]),
                    note: L("信用卡消费在还款日才从现金账户扣出；每天的花销按消费日算。")
                ) {
                    CashTimelineChart(start: first, end: last, days: days, onPickDay: { model.transactionDetail = TransactionDetail(month: $0, currency: shown) }, currency: shown)
                } key: {
                    TimelineKey(projected: false)
                }
            }
        }
        .padding(.top, 4)
    }

    private func cashTitle(_ s: MonthSummary, days: [CashFlowDay], open: Double?, close: Double?) -> String {
        var parts = [L("收入 {x}", ["x": amount(s.income)])]
        if let big = days.max(by: { $0.outflow.double < $1.outflow.double }), big.outflow.double > 0 {
            parts.append(L("花得最多的一天是 {d}（{x}）", ["d": Format.day(big.date), "x": amount(big.outflow)]))
        }
        if let open, let close {
            let d = close - open
            parts.append(d >= 0 ? L("现金账户月底比月初多 {x}", ["x": amount(d)]) : L("现金账户月底比月初少 {x}", ["x": amount(-d)]))
        }
        return parts.joined(separator: L("；"))
    }

    // MARK: exhibit titles: each states its finding

    private var monthsTitle: String {
        let s = report?.series ?? []
        let income = s.reduce(0.0) { $0 + $1.income.double }
        let expenses = s.reduce(0.0) { $0 + $1.expenses.double }
        guard income > 0 else { return L("每个月挣多少、花多少") }
        return L("过去 {n} 个月合计存下收入的 {p}", ["n": s.count, "p": Format.percent((income - expenses) / income)])
    }

    private var networthTitle: String {
        let pts = worthPoints
        guard let last = pts.last, pts.count >= 2, pts.allSatisfy({ ($0.missingFx ?? []).isEmpty }) else { return L("我一共有多少") }
        let back = pts[max(0, pts.count - 13)]
        let months = pts.count - 1 - max(0, pts.count - 13)
        let d = last.netWorth.double - back.netWorth.double
        let change = Format.money(Decimal(abs(d)), currency: shown, digits: 0)
        return months >= 12
            ? (d >= 0 ? L("净资产一年多了 {x}", ["x": change]) : L("净资产一年少了 {x}", ["x": change]))
            : (d >= 0 ? L("净资产 {n} 个月多了 {x}", ["n": months, "x": change]) : L("净资产 {n} 个月少了 {x}", ["n": months, "x": change]))
    }

    private func amount(_ value: Money) -> String { m0(value, currency: shown) }
    private func amount(_ value: Double) -> String { m0(value, currency: shown) }
}
