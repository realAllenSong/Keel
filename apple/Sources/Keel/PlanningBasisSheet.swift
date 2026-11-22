import KeelCore
import SwiftUI

struct PlanningBasisSheet: View {
    @Environment(\.dismiss) private var dismiss
    let basis: PlanningBasis
    let apply: (Double, Double) -> Void
    @State private var income: Set<String>
    @State private var spending: Set<String>
    @State private var invalidFields: Set<String> = []
    @State private var amounts: [String: Double]
    /// Fewer than 12 months of history: show a year's worth, unless the income was short-term.
    @State private var annualize: Bool

    init(basis: PlanningBasis, apply: @escaping (Double, Double) -> Void) {
        self.basis = basis
        self.apply = apply
        _income = State(initialValue: basis.selectedIncome)
        _spending = State(initialValue: basis.selectedSpending)
        _amounts = State(initialValue: Dictionary(uniqueKeysWithValues: (basis.income + basis.spending).map { ($0.category, $0.annualAmount) }))
        _annualize = State(initialValue: basis.months > 0 && basis.months < 12)
    }

    private var scale: Double { annualize && basis.months > 0 && basis.months < 12 ? 12 / Double(basis.months) : 1 }

    private func total(_ selected: Set<String>) -> Double {
        Double(selected.reduce(Int64(0)) { $0 + Int64(((amounts[$1] ?? 0) * 100).rounded()) }) / 100
    }
    private var valid: Bool { invalidFields.isEmpty && amounts.values.allSatisfy { $0.isFinite && abs($0) < 1_000_000_000 } && total(income) >= 0 && total(spending) >= 0 }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 6) {
                    Text(L("核对持续收支")).font(.display(24))
                    Text(L("历史流水只是参考；勾选未来会持续发生的项目，并填写预计年金额。")).font(.ui(12)).foregroundStyle(Theme.muted)
                }
                Spacer()
                Button(L("关闭")) { dismiss() }.buttonStyle(.keelOutlineCompact).keyboardShortcut(.cancelAction)
            }.padding(24)
            Hairline()
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text(L("过去 12 个月截至 {m}，其中 {n} 个月有流水。下面是这几个月实际发生的合计。", ["m": Format.month(basis.asOf), "n": basis.months])).font(.ui(12)).foregroundStyle(Theme.muted)
                    if basis.months > 0 && basis.months < 12 {
                        Toggle(isOn: $annualize) {
                            Text(L("按 {n} 个月折算成一整年（实习这类短期收入不要折算）", ["n": basis.months])).font(.ui(12.5)).foregroundStyle(Theme.ink)
                        }
                        .toggleStyle(.checkbox)
                    }
                    section(L("收入"), rows: basis.income, selected: $income)
                    section(L("支出"), rows: basis.spending, selected: $spending)
                    Text(L("汇款、投资收入、学费、旅行、取现及未分类默认不勾选；如会持续发生，可自行加入。未勾选项目仍保留在账本中。")).font(.ui(12)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                }.padding(24)
            }
            Hairline()
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(L("预计每年：收入 {i} · 支出 {e}", ["i": m0(total(income) * scale), "e": m0(total(spending) * scale)])).font(.ui(14, .medium))
                    Text(L("之后仍可在规划页直接调整，包括换工作后的收入。")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                }
                Spacer()
                Button(L("应用持续收支")) { apply(total(income) * scale, total(spending) * scale); dismiss() }
                    .buttonStyle(.keelPrimaryCompact).disabled(!valid)
            }.padding(24)
        }
        .frame(width: 790, height: 700).background(Theme.card)
    }

    private func section(_ title: String, rows: [PlanningBasisLine], selected: Binding<Set<String>>) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(title).font(.ui(15, .medium)); Spacer()
                Text(L("历史 12 个月")).frame(width: 125, alignment: .trailing)
                Text(L("预计每年")).frame(width: 135, alignment: .trailing)
            }.font(.ui(11.5)).foregroundStyle(Theme.muted)
            ForEach(rows) { row in
                HStack(spacing: 12) {
                    Toggle(Format.account(row.category), isOn: Binding(get: { selected.wrappedValue.contains(row.category) }, set: { on in
                        if on { selected.wrappedValue.insert(row.category) } else { selected.wrappedValue.remove(row.category) }
                    })).toggleStyle(.checkbox).font(.ui(12.5)).frame(maxWidth: .infinity, alignment: .leading)
                    Text(m0(row.annualAmount)).font(.mono(12)).foregroundStyle(Theme.muted).frame(width: 125, alignment: .trailing)
                    PlanningAmountField(value: amounts[row.category] ?? 0, fieldID: row.category, invalidFields: $invalidFields, allowNegative: true, width: 125) { amounts[row.category] = $0 }
                        .frame(width: 135, alignment: .trailing)
                }
            }
            if rows.isEmpty { Text(L("没有历史流水，可在规划页手填预期。")).font(.ui(12)).foregroundStyle(Theme.muted) }
        }
    }
}
