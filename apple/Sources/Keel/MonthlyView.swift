import KeelCore
import SwiftUI

/// The existing monthly pages keep their own statements and charts. Year is another period,
/// not another destination in the main navigation.
struct MonthlyView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Group {
            if model.showAnnual { AnnualView() }
            else if model.place == .report { ReportView() }
            else { NowView() }
        }
    }
}

struct MonthlyPeriodControls: View {
    @Environment(AppModel.self) private var model
    @AppStorage("keel.netWorthCurrency") private var currency = "USD"
    let month: String

    var body: some View {
        HStack(spacing: 12) {
            Segmented(items: [(false, L("月")), (true, L("年"))], selection: Binding(
                get: { model.showAnnual },
                set: { value in
                    model.summaryYear = Int(month.prefix(4)) ?? model.summaryYear
                    model.showAnnual = value
                }
            ), fill: true)
            ReportCurrencyPicker(currency: $currency)
            HStack(spacing: 8) {
                Button(L("上个月")) { model.selectSummaryMonth(AppModel.shift(month, -1)) }
                    .buttonStyle(.keelOutline).disabled(month <= (model.firstMonth ?? "1900-01"))
                Text(Format.month(month)).font(.ui(13)).foregroundStyle(Theme.ink)
                Button(L("下个月")) { model.selectSummaryMonth(AppModel.shift(month, 1)) }
                    .buttonStyle(.keelOutline).disabled(month >= (model.currentMonth ?? ReportingPeriod.month()))
            }
        }
    }
}

/// A compact replacement for the unwanted upcoming-payments sidebar, reusing the asset page.
struct MonthlyAssets: View {
    @Environment(AppModel.self) private var model
    @AppStorage("keel.netWorthCurrency") private var currency = "USD"
    let month: String
    @State private var data = Loader<BalanceSheet>()
    @State private var showing = false

    private var date: String { ReportingPeriod.end(of: month, through: model.today) }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .firstTextBaseline) {
                Text(L("资产")).font(.serif(22)).foregroundStyle(Theme.ink)
                Spacer()
                Button(L("查看账户")) { showing = true }.buttonStyle(.keelOutlineCompact)
            }
            Text(L("账本净资产")).font(.ui(12)).foregroundStyle(Theme.muted)
            if let b = data.value, b.date == date {
                let shown = b.currency ?? currency  // what is on screen until the new currency arrives
                let missing = b.missingFx ?? []
                let assets = b.lines.filter { $0.type == "Assets" }
                Button(missing.isEmpty ? Format.money(b.netWorth, currency: shown, digits: 2) : "—") { showing = true }
                    .font(.serif(34)).foregroundStyle(Theme.ink).buttonStyle(.plain)
                Text(L("截至 {d}", ["d": Format.day(b.date)])).font(.ui(11)).foregroundStyle(Theme.muted)
                Hairline()
                balanceRow(L("资产合计"), totals: NativeBalances.total(assets))
                ForEach(AssetsView.order, id: \.self) { key in
                    let lines = assets.filter { AssetsView.classKey($0.assetClass ?? "other") == key }
                    if !lines.isEmpty {
                        balanceRow(NetworthChart.classes.first { $0.key == key }?.label ?? key, totals: NativeBalances.total(lines))
                    }
                }
                let debts = NativeBalances.total(b.lines.filter { $0.type == "Liabilities" })
                if !debts.isEmpty { balanceRow(L("欠款"), totals: debts) }
                if !missing.isEmpty {
                    AssetMissingRates(pairs: missing)
                }
            } else if let error = data.error { ErrorLine(message: error) }
            else { SkeletonRows(count: 3) }
        }
        .frame(width: 380, alignment: .topLeading).flatSection()
        .task(id: "\(model.revision)|\(date)|\(currency)") { [date, currency] in
            await data.load { try await $0.balanceSheet(date: date, currency: currency) }
        }
        .sheet(isPresented: $showing) { AssetDetailsSheet(month: month).environment(model) }
    }

    private func balanceRow(_ title: String, totals: [String: Decimal]) -> some View {
        HStack {
            Text(title).font(.ui(12)).foregroundStyle(Theme.muted)
            Spacer()
            Button { showing = true } label: {
                NativeBalances(amounts: totals).font(.mono(12)).foregroundStyle(Theme.ink)
            }.buttonStyle(.plain)
        }
    }
}

struct AssetDetailsSheet: View {
    @Environment(\.dismiss) private var dismiss
    let month: String

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(L("资产") + " · " + Format.month(month)).font(.display(21)).foregroundStyle(Theme.ink)
                Spacer()
                Button(L("关闭")) { dismiss() }.buttonStyle(.keelOutlineCompact).keyboardShortcut(.cancelAction)
            }.padding(24)
            Hairline()
            Page(width: 1040) { AssetsView(selectedMonth: month) }
        }
        .frame(width: 1100, height: 760).background(Theme.paper)
    }
}
