import KeelCore
import SwiftUI

/// 账单: every recurring charge found in the ledger (rent, phone, subscriptions, annual fees),
/// what leaves each month no matter what, and the regular income. "不算" says a series is not a
/// bill: it leaves this list and the forecast, and is not suggested again.
struct BillsView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("keel.netWorthCurrency") private var currency = "USD"
    @State private var data = Loader<Bills>("bills")
    @State private var showDismissed = false

    private var shown: String { data.value?.currency ?? currency }

    var body: some View {
        VStack(alignment: .leading, spacing: 36) {
            if let error = data.error { ErrorLine(message: L("账单暂时读不到：{e}", ["e": error])) }
            if let b = data.value {
                if b.monthly.isEmpty && b.annual.isEmpty && b.income.isEmpty && b.dismissed.isEmpty {
                    empty
                } else {
                    head(b)
                    // Numbered in the order shown: a page without yearly bills goes 01, 02.
                    let monthlyN = 1
                    let annualN = (b.monthly.isEmpty ? 0 : 1) + 1
                    let incomeN = (b.monthly.isEmpty ? 0 : 1) + (b.annual.isEmpty ? 0 : 1) + 1
                    if !b.monthly.isEmpty { section(monthlyN, L("每月"), b.monthly, when: L("每月几号"), total: monthlyTotal(b)) }
                    if !b.annual.isEmpty { section(annualN, L("每年"), b.annual, when: L("每年几月"), note: yearlyNote(b)) }
                    if !b.income.isEmpty { section(incomeN, L("固定收入"), b.income, when: L("每月几号"), dismissable: false) }
                    if !b.dismissed.isEmpty { dismissed(b.dismissed) }
                }
            } else if data.error == nil {
                SkeletonRows(count: 6)
            }
        }
        .task(id: "\(model.revision)|\(currency)") { [currency] in
            await data.load { try await $0.bills(currency: currency) }
        }
    }

    // MARK: head

    private func head(_ b: Bills) -> some View {
        let count = b.monthly.count + b.annual.count
        let subs = (b.monthly + b.annual).filter(\.subscription).count
        var facts = [L("{n} 项固定支出", ["n": count])]
        if subs > 0 { facts.append(L("其中 {n} 项是订阅", ["n": subs])) }
        return VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .center, spacing: 16) {
                Text(facts.joined(separator: " · ")).font(.ui(13)).foregroundStyle(Theme.muted)
                Spacer(minLength: 16)
                ReportCurrencyPicker(currency: $currency)
            }
            Text(L("每月固定支出 {m}，一年约 {y}", [
                "m": Format.money(b.monthlyTotal, currency: shown),
                "y": Format.money(b.yearlyTotal, currency: shown),
            ]))
            .font(.serif(40)).foregroundStyle(Theme.ink).fixedSize(horizontal: false, vertical: true)
            Text(L("从流水里找出来的：同一个商户、每月差不多同一天、金额相近。看着不像固定支出的，点「不算」。"))
                .font(.ui(12.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            if !b.missingFx.isEmpty { AssetMissingRates(pairs: b.missingFx) }
        }
    }

    private var empty: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(L("还没有发现固定支出")).font(.serif(40)).foregroundStyle(Theme.ink)
            Text(L("同一笔扣款连续出现两三个月后（房租、话费、订阅），会自动列在这里。"))
                .font(.ui(14)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
        }
    }

    private func monthlyTotal(_ b: Bills) -> String? {
        // Only when every row is in the report currency: a sum of mixed currencies means nothing.
        guard b.monthly.allSatisfy({ $0.currency == shown }) else { return nil }
        let sum = b.monthly.reduce(Decimal(0)) { $0 + $1.amount.value }
        return Format.money(sum, currency: shown, digits: 2)
    }

    private func yearlyNote(_ b: Bills) -> String? {
        guard b.annual.allSatisfy({ $0.currency == shown }) else { return nil }
        let sum = b.annual.reduce(Decimal(0)) { $0 + $1.amount.value }
        return L("折合每月 {v}", ["v": Format.money(sum / 12, currency: shown, digits: 2)])
    }

    // MARK: sections

    private func section(
        _ number: Int, _ title: String, _ rows: [BillSeries], when: String,
        total: String? = nil, note: String? = nil, dismissable: Bool = true
    ) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                SectionTitle(number: number, title: title)
                Spacer()
                if let note { Text(note).font(.ui(12)).foregroundStyle(Theme.muted) }
            }
            Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 0) {
                GridRow {
                    Text(L("名称"))
                    Text(L("类别"))
                    Text(L("扣款账户"))
                    Text(when).gridColumnAlignment(.trailing)
                    Text(L("上次"))
                    Text(L("下次"))
                    Text(L("金额")).gridColumnAlignment(.trailing)
                    Text("")
                }
                .font(.ui(11.5)).foregroundStyle(Theme.muted).padding(.bottom, 8)
                ForEach(rows) { s in
                    GridRow { Hairline(color: Theme.rule).gridCellColumns(8) }
                    GridRow(alignment: .firstTextBaseline) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(Format.tidy(s.name)).font(.ui(13.5, .medium)).foregroundStyle(Theme.ink).lineLimit(1)
                            if let c = s.priceChange {
                                Text(L("上次扣款从 {a} 变成 {b}", [
                                    "a": Format.money(c.before, currency: s.currency, digits: 2),
                                    "b": Format.money(c.now, currency: s.currency, digits: 2),
                                ]))
                                .font(.ui(11.5)).foregroundStyle(c.now.value > c.before.value ? Theme.signal : Theme.muted)
                            } else if s.new {
                                Text(L("刚开始：目前只见到两次扣款")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)  // every table spans the column
                        Text(Format.account(s.category)).font(.ui(12.5)).foregroundStyle(Theme.ink2).lineLimit(1)
                        Text(Format.account(s.account)).font(.ui(12.5)).foregroundStyle(Theme.ink2).lineLimit(1)
                        Text(s.cadence == "annual" ? L("{m} 月", ["m": s.month ?? 0]) : L("{d} 日", ["d": s.day]))
                            .font(.mono(13)).foregroundStyle(Theme.ink)
                        Text(Format.day(s.lastDate)).font(.mono(12.5)).foregroundStyle(Theme.muted)
                        next(s)
                        Text(Format.money(s.amount, currency: s.currency, digits: 2))
                            .font(.mono(13)).foregroundStyle(Theme.ink)
                        if dismissable {
                            LinkButton(title: L("不算"), arrow: false) { change { try await $0.dismissBill(s.key, name: s.name, currency: currency) } }
                        } else {
                            Text("")
                        }
                    }
                    .padding(.vertical, 8)
                }
                if let total {
                    GridRow { Hairline(color: Theme.rule).gridCellColumns(8) }
                    GridRow {
                        Text("").gridCellColumns(5)
                        Text(L("每月合计")).font(.ui(12.5, .medium)).foregroundStyle(Theme.ink)
                        Text(total).font(.mono(13.5, .medium)).foregroundStyle(Theme.ink)
                        Text("")
                    }
                    .padding(.top, 10)
                }
            }
        }
        .flatSection()
    }

    @ViewBuilder
    private func next(_ s: BillSeries) -> some View {
        if let late = s.overdueDays, late > 0 {
            Text(L("已过 {n} 天", ["n": late])).font(.ui(12.5)).foregroundStyle(Theme.signal)
        } else if let day = s.nextDate {
            let past = day < model.today
            // Expected already, but this account's statement does not reach that day yet.
            Text(past ? L("等账单导入") : Format.day(day) + (day.prefix(4) == model.today.prefix(4) ? "" : " " + day.prefix(4)))
                .font(past ? .ui(12.5) : .mono(12.5)).foregroundStyle(past ? Theme.muted : Theme.ink)
        } else {
            Text(L("等账单导入")).font(.ui(12.5)).foregroundStyle(Theme.muted)
        }
    }

    private func dismissed(_ rows: [BillSeries]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Text(L("已忽略 {n} 项", ["n": rows.count])).font(.ui(12)).foregroundStyle(Theme.muted)
                Text("·").font(.ui(12)).foregroundStyle(Theme.muted)
                LinkButton(title: showDismissed ? L("收起") : L("查看"), arrow: false) { showDismissed.toggle() }
            }
            if showDismissed {
                ForEach(rows) { s in
                    HStack(spacing: 14) {
                        Text(Format.tidy(s.name)).font(.ui(13)).foregroundStyle(Theme.ink2)
                        Text(Format.money(s.amount, currency: s.currency, digits: 2)).font(.mono(12.5)).foregroundStyle(Theme.muted)
                        LinkButton(title: L("恢复"), arrow: false) { change { try await $0.restoreBill(s.key, currency: currency) } }
                    }
                }
            }
        }
    }

    private func change(_ call: @escaping (API) async throws -> Bills) {
        guard let api = model.api else { return }
        Task {
            do {
                data.set(try await call(api))
                model.revision += 1  // the forecast and the calendar follow
            } catch {
                model.banner = L("没保存：{e}", ["e": error.localizedDescription])
            }
        }
    }
}
