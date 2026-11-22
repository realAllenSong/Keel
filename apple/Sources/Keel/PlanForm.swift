import KeelCore
import SwiftUI

/// The plan's inputs, prefilled from the ledger: money in and out, the allocation, life events,
/// and (folded away) the assumptions behind the simulation.
struct PlanForm: View {
    @Environment(AppModel.self) private var model
    let store: PlanStore
    let state: PlanState
    @State private var advanced = false
    @State private var saving = false
    @State private var reviewingCashflow = false
    @State private var comparingEmployment = false
    @State private var showingRetirement = false
    @State private var invalidRetirementFields: Set<String> = []
    /// Inside the assumptions panel: stages and events are listed there already, and the panel
    /// is the card.
    var embedded = false

    private var s: Scenario { state.scenario }
    private func up(_ change: (inout Scenario) -> Void) { store.update { change(&$0.scenario) } }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !embedded {
                Text(store.isCustom ? L("你的计划（自定义情景）") : L("你的计划（从账本预填）"))
                    .font(.ui(15, .medium)).foregroundStyle(Theme.ink)
                Text(store.isCustom ? L("右侧结果按下方输入计算。") : L("起点：{x}", ["x": derivedFrom])).font(.ui(11.5)).foregroundStyle(Theme.muted)
                    .padding(.top, 6).padding(.bottom, 20)
            }

            if let f = store.defaults?.facts { FactsBox(facts: f) }
            if store.defaults?.planningBasis != nil {
                Button(L("核对持续收支")) { reviewingCashflow = true }.buttonStyle(.keelOutlineCompact).padding(.bottom, 12)
            }
            if state.cashflowReviewed == nil {
                Text(L("已保留原有计划数值；建议核对一次持续收支。")).font(.ui(11.5)).foregroundStyle(Theme.muted).padding(.bottom, 12)
            }
            if !embedded { stages }
            FormSection(title: (s.phases ?? []).isEmpty ? L("你的预期 · 今天的美元") : L("阶段之后的长期预期 · 今天的美元")) {
                Field(label: L("可投资资产"), hint: L("先扣除需偿还债务和另留的备用金")) { NumberField(value: s.initial) { v in up { $0.initial = v } } }
                Field(label: L("预计年收入（税后）"), hint: L("按你接下来的打算填")) {
                    NumberField(value: s.annualIncome) { v in up { $0.annualIncome = v } }
                }
                Button(L("税后收入计算（纽约或中国工作）")) { comparingEmployment = true }.buttonStyle(.keelOutlineCompact)
                Field(label: L("预计年开销")) { NumberField(value: s.annualSpending) { v in up { $0.annualSpending = v } } }
                Field(label: L("再工作几年"), hint: L("{n} 年", ["n": Int(s.workYears)])) {
                    KeelSlider(value: s.workYears, range: 0...45, step: 1) { v in up { $0.workYears = v } }
                }
            }
            if !state.projectionReady {
                Button(L("使用当前收支假设")) { store.update { $0.cashflowReviewed = true } }.buttonStyle(.keelOutlineCompact).padding(.bottom, 16)
            }
            allocation
            retirementAccount
            if !embedded { events }
            DisclosureRow(title: L("更多假设"), open: $advanced)
            if advanced { assumptions }
            SavedPlans(store: store, state: state)
            Button(L("恢复账本数据")) { store.reset() }
                .buttonStyle(.keelOutlineCompact)
                .frame(maxWidth: .infinity)
                .padding(.top, 16)
        }
        .padding(embedded ? 0 : 20)
        .background(embedded ? Color.clear : Theme.card, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(embedded ? Color.clear : Theme.border))
        .sheet(isPresented: $reviewingCashflow) {
            if let basis = store.defaults?.planningBasis {
                PlanningBasisSheet(basis: basis) { income, spending in
                    store.update { $0.scenario.annualIncome = income; $0.scenario.annualSpending = spending; $0.cashflowReviewed = true }
                }
            }
        }
        .sheet(isPresented: $comparingEmployment) { EmploymentComparisonSheet(store: store, state: state) }
    }

    private var derivedFrom: String {
        guard let d = store.defaults?.derivedFrom else { return L("账本") }
        return d == "empty ledger" ? L("先试算，再导入账本") : d
    }

    private var retirementAccount: some View {
        VStack(alignment: .leading, spacing: 8) {
            DisclosureRow(title: L("401(k) / 税延退休账户"), open: $showingRetirement)
            if showingRetirement || (s.hasRetirementAccount && state.retirementReviewed != true) {
                Text(L("当前退休余额是上方可投资资产的一部分，不会再加一次。未来缴款单独累积；提取时先用流动资金，再对退休账户按输入税率计税。")).font(.ui(11.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                retirementField(L("当前税延余额"), value: s.retirementBalance ?? 0) { $0.retirementBalance = $1 }
                retirementField(L("未来每年税前缴款"), value: s.annualRetirementContribution ?? 0) { $0.annualRetirementContribution = $1 }
                retirementField(L("多少年后可提取"), value: s.retirementAccessYear) { $0.retirementAccessYear = $1 }
                retirementField(L("提取的有效税率（%）"), value: s.retirementWithdrawalTaxRate.map { $0 * 100 }) { $0.retirementWithdrawalTaxRate = $1 / 100 }
                Text(L("请自行核对可提取时点；例如通常的 59½ 岁规则及例外。0 年表示现在可取，0% 表示免税。未来税率是情景假设，不是固定税务结论。")).font(.ui(11.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                Button(L("确认退休账户假设")) { store.update { $0.retirementReviewed = true } }.buttonStyle(.keelOutlineCompact)
                    .disabled(!invalidRetirementFields.isEmpty || (s.retirementBalance ?? 0) > s.initial || s.retirementAccessYear == nil || s.retirementWithdrawalTaxRate == nil || (s.retirementAccessYear ?? 0) > 80 || (s.retirementWithdrawalTaxRate ?? 0) > 1)
                if (s.retirementBalance ?? 0) > s.initial { Text(L("退休余额不能超过可投资资产总额。")).font(.ui(11.5)).foregroundStyle(Theme.outflow) }
            }
        }.padding(.vertical, 16)
    }

    private func retirementField(_ title: String, value: Double?, change: @escaping (inout Scenario, Double) -> Void) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.ui(12)).foregroundStyle(Theme.muted)
            PlanningAmountField(value: value, fieldID: title, invalidFields: $invalidRetirementFields, onDraftChange: { store.update { $0.retirementReviewed = false } }) { v in
                store.update { change(&$0.scenario, v); $0.retirementReviewed = false }
            }
        }
    }

    // MARK: allocation (the other two rebalance to 100%)

    private var allocation: some View {
        let stocks = Int(((s.weights["stocks"] ?? 0) * 100).rounded())
        let bonds = Int(((s.weights["bonds"] ?? 0) * 100).rounded())
        let cash = 100 - stocks - bonds
        return FormSection(title: L("股 / 债 / 现金")) {
            Field(label: L("股票"), hint: "\(stocks)%") {
                KeelSlider(value: Double(stocks), range: 0...100, step: 5) { v in setAlloc(Int(v), bonds) }
            }
            Field(label: L("债券"), hint: "\(bonds)%") {
                KeelSlider(value: Double(bonds), range: 0...100, step: 5) { v in setAlloc(min(stocks, 100 - Int(v)), Int(v)) }
            }
            Field(label: L("现金"), hint: "\(cash)%") {
                KeelSlider(value: Double(cash), range: 0...100, step: 5) { v in
                    let invested = stocks + bonds
                    let st = invested > 0 ? Int((Double(100 - Int(v)) * Double(stocks) / Double(invested)).rounded()) : 100 - Int(v)
                    setAlloc(st, 100 - Int(v) - st)
                }
            }
            Text(L("调整一项时，其余比例自动平衡到 100%。")).font(.ui(11.5)).foregroundStyle(Theme.muted)
        }
    }

    private func setAlloc(_ stocks: Int, _ bonds: Int) {
        let b = min(bonds, 100 - stocks)
        up { $0.weights = ["stocks": Double(stocks) / 100, "bonds": Double(b) / 100, "cash": Double(100 - stocks - b) / 100] }
    }

    // MARK: life stages

    private var stages: some View {
        let list = s.phases ?? []
        return FormSection(title: L("人生阶段 · 每月")) {
            Text(L("阶段内按这里的月收入和月支出算，替换下方的年收入、年开销；阶段之后回到下方的长期预期。"))
                .font(.ui(11.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            ForEach(Array(list.enumerated()), id: \.offset) { i, p in
                PhaseEditor(phase: p) { changed in
                    up { sc in
                        var l = sc.phases ?? []
                        if let changed { l[i] = changed } else { l.remove(at: i) }
                        sc.phases = l
                    }
                }
            }
            Button(L("＋ 添加阶段")) {
                let start = list.last.map { Self.nextMonth($0.end) } ?? ReportingPeriod.month()
                up { $0.phases = ($0.phases ?? []) + [PlanPhase(label: L("新阶段"), start: start, end: Self.nextMonth(start, by: 11), monthlyIncome: 0, monthlySpending: 0)] }
            }
            .buttonStyle(.keelOutlineCompact)
        }
    }

    static func nextMonth(_ ym: String, by k: Int = 1) -> String {
        let parts = ym.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 2 else { return ym }
        let n = parts[0] * 12 + parts[1] - 1 + k
        return String(format: "%04d-%02d", n / 12, n % 12 + 1)
    }

    // MARK: life events

    private var events: some View {
        let list = s.events ?? []
        return FormSection(title: L("人生大事（负数 = 支出）")) {
            ForEach(Array(list.enumerated()), id: \.offset) { i, e in
                EventEditor(event: e) { changed in
                    up { sc in
                        var l = sc.events ?? []
                        if let changed { l[i] = changed } else { l.remove(at: i) }
                        sc.events = l
                    }
                }
            }
            Button(L("＋ 添加事项")) { add(LifeEvent(label: L("人生大事"), year: 1, amount: 0)) }
                .buttonStyle(.keelOutlineCompact)
            HStack(spacing: 6) {
                ForEach(presets, id: \.label) { e in
                    Button("＋ " + (e.label ?? "")) { add(e) }.buttonStyle(.keelOutlineCompact)
                }
            }
        }
    }

    private var presets: [LifeEvent] {
        [
            LifeEvent(label: L("买房首付"), year: 5, amount: -150_000),
            LifeEvent(label: L("换车"), year: 3, amount: -40_000, every: 8, times: 4),
            LifeEvent(label: L("孩子大学"), year: 18, amount: -60_000, every: 1, times: 4),
        ]
    }

    private func add(_ e: LifeEvent) { up { $0.events = ($0.events ?? []) + [e] } }

    // MARK: assumptions

    private var assumptions: some View {
        VStack(alignment: .leading, spacing: 0) {
            FormSection(title: L("收入增长与再平衡")) {
                Field(label: L("收入实际增长"), hint: L("{p}%/年", ["p": String(format: "%.1f", (s.incomeGrowth ?? 0.01) * 100)])) {
                    KeelSlider(value: (s.incomeGrowth ?? 0.01) * 100, range: -2...6, step: 0.5) { v in up { $0.incomeGrowth = v / 100 } }
                }
                Field(label: L("再平衡")) {
                    Segmented(
                        items: [("annual", L("每年")), ("monthly", L("每月")), ("none", L("不再平衡"))],
                        selection: Binding(get: { s.rebalance ?? "annual" }, set: { v in up { $0.rebalance = v } }), fill: true
                    )
                }
            }
            FormSection(title: L("退休后")) {
                Field(label: L("退休后年开销（空 = 同上）")) {
                    NumberField(value: s.retirementSpending, optional: true, set: { v in up { $0.retirementSpending = v } })
                }
                Field(label: L("提款方式")) {
                    Segmented(
                        items: [("constant", L("固定金额")), ("percent", L("固定比例")), ("guardrails", L("护栏"))],
                        selection: Binding(get: { s.withdrawal ?? "constant" }, set: { v in up { $0.withdrawal = v } }), fill: true
                    )
                }
                Field(label: L("提款率"), hint: String(format: "%.2f%%", (s.withdrawalRate ?? 0.04) * 100)) {
                    KeelSlider(value: (s.withdrawalRate ?? 0.04) * 100, range: 2...7, step: 0.25) { v in up { $0.withdrawalRate = v / 100 } }
                }
                HStack(spacing: 12) {
                    Field(label: L("养老金/社保 /年")) { NumberField(value: s.pension ?? 0) { v in up { $0.pension = v } } }
                    Field(label: L("从第几年开始")) { NumberField(value: s.pensionStartYear ?? 99) { v in up { $0.pensionStartYear = v } } }
                }
            }
            FormSection(title: L("市场模型")) {
                Segmented(
                    items: [("bootstrap", L("历史分块重抽样")), ("student_t", L("厚尾 t 分布"))],
                    selection: Binding(get: { state.model.kind ?? "bootstrap" }, set: { v in store.update { $0.model.kind = v } }), fill: true
                )
                Field(label: L("历史区间起点")) {
                    TextInput(text: state.model.windowStart ?? "1928-01") { v in store.update { $0.model.windowStart = v } }
                }
                Field(label: L("模拟年数"), hint: L("{n} 年", ["n": s.horizonYears ?? 50])) {
                    KeelSlider(value: Double(s.horizonYears ?? 50), range: 10...70, step: 5) { v in up { $0.horizonYears = Int(v) } }
                }
            }
        }
    }
}

// MARK: - form pieces

private struct FormSection<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title.uppercased()).font(.ui(10.5)).tracking(1.6).foregroundStyle(Theme.muted)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.bottom, 20)
        .overlay(alignment: .bottom) { Hairline() }
        .padding(.bottom, 20)
    }
}

struct Field<Content: View>: View {
    let label: String
    var hint: String?
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(label).font(.ui(11.5)).foregroundStyle(Theme.muted)
                Spacer()
                if let hint { Text(hint).font(.mono(11)).foregroundStyle(Theme.muted) }
            }
            content
        }
    }
}

/// A number typed as text; applied as soon as it parses (empty = nil when optional).
struct NumberField: View {
    let value: Double?
    var optional = false
    var width: CGFloat? = nil
    let onChange: (Double?) -> Void
    @State private var text = ""

    init(value: Double?, optional: Bool = false, width: CGFloat? = nil, onChange: @escaping (Double) -> Void) {
        self.value = value
        self.optional = optional
        self.width = width
        self.onChange = { if let v = $0 { onChange(v) } }
        _text = State(initialValue: Self.format(value))
    }

    init(value: Double?, optional: Bool, set: @escaping (Double?) -> Void) {
        self.value = value
        self.optional = optional
        self.onChange = set
        _text = State(initialValue: Self.format(value))
    }

    var body: some View {
        KeelField(placeholder: optional ? "—" : "0", text: $text, width: width, mono: true)
            .onChange(of: value) { _, v in if Double(text) != v { text = Self.format(v) } }
            .onChange(of: text) { _, t in
                // Only typing reaches the model; a value pushed in from outside just redraws.
                guard t != Self.format(value) else { return }
                let trimmed = t.trimmingCharacters(in: .whitespaces)
                if trimmed.isEmpty, optional { onChange(nil) } else if let v = Double(trimmed), v != value { onChange(v) }
            }
    }

    static func format(_ v: Double?) -> String {
        guard let v else { return "" }
        return v == v.rounded() ? String(Int(v)) : String(format: "%.2f", v)
    }
}

struct TextInput: View {
    let text: String
    let onChange: (String) -> Void
    @State private var value: String

    init(text: String, onChange: @escaping (String) -> Void) {
        self.text = text
        self.onChange = onChange
        _value = State(initialValue: text)
    }

    var body: some View {
        KeelField(placeholder: "", text: $value, width: nil, mono: true)
            .onChange(of: text) { _, t in if t != value { value = t } }
            .onChange(of: value) { _, v in if v != text { onChange(v) } }
    }
}

/// A thin ink slider.
struct KeelSlider: View {
    let value: Double
    let range: ClosedRange<Double>
    let step: Double
    let onChange: (Double) -> Void

    var body: some View {
        Slider(value: Binding(get: { value }, set: { onChange(($0 / step).rounded() * step) }), in: range)
            .controlSize(.small)
            .tint(Theme.ink)
    }
}

struct DisclosureRow: View {
    let title: String
    @Binding var open: Bool

    var body: some View {
        Button { withAnimation(.easeOut(duration: 0.15)) { open.toggle() } } label: {
            HStack(spacing: 6) {
                Image(systemName: open ? "chevron.down" : "chevron.right").font(.ui(10, .semibold))
                Text(title).font(.ui(13))
            }
            .foregroundStyle(Theme.muted)
            .padding(.vertical, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

struct EventEditor: View {
    let event: LifeEvent
    let onChange: (LifeEvent?) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .bottom, spacing: 8) {
                Field(label: L("事项")) {
                    TextInput(text: event.label ?? "") { v in var e = event; e.label = v; onChange(e) }
                }
                if let date = event.date {
                    Field(label: L("月份")) {
                        TextInput(text: date) { v in var e = event; e.date = v; onChange(e) }
                    }
                    .frame(width: 76)
                } else {
                    Field(label: L("第几年")) {
                        NumberField(value: event.year, width: 52) { v in var e = event; e.year = v; onChange(e) }
                    }
                    .frame(width: 60)
                }
                Field(label: L("金额")) {
                    NumberField(value: event.amount, width: 84) { v in var e = event; e.amount = v; onChange(e) }
                }
                .frame(width: 92)
                Button { onChange(nil) } label: {
                    Image(systemName: "xmark").font(.ui(10, .semibold)).foregroundStyle(Theme.muted).frame(width: 16, height: 28)
                }
                .buttonStyle(.plain)
                .help(L("删除事项"))
            }
            HStack(spacing: 6) {
                Text(L("每")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                NumberField(value: event.every, optional: true, set: { v in var e = event; e.every = v; onChange(e) })
                    .frame(width: 56)
                Text(L("年重复，共")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                NumberField(value: Double(event.times ?? 1), width: 48) { v in var e = event; e.times = Int(v); onChange(e) }
                Text(L("次")).font(.ui(11.5)).foregroundStyle(Theme.muted)
            }
        }
    }
}

struct PhaseEditor: View {
    let phase: PlanPhase
    let onChange: (PlanPhase?) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .bottom, spacing: 8) {
                Field(label: L("阶段")) {
                    TextInput(text: phase.label) { v in var p = phase; p.label = v; onChange(p) }
                }
                Button { onChange(nil) } label: {
                    Image(systemName: "xmark").font(.ui(10, .semibold)).foregroundStyle(Theme.muted).frame(width: 16, height: 28)
                }
                .buttonStyle(.plain)
                .help(L("删除阶段"))
            }
            HStack(spacing: 8) {
                Field(label: L("从")) { TextInput(text: phase.start) { v in var p = phase; p.start = v; onChange(p) } }
                Field(label: L("到（含）")) { TextInput(text: phase.end) { v in var p = phase; p.end = v; onChange(p) } }
            }
            HStack(spacing: 8) {
                Field(label: L("月收入（税后）")) {
                    NumberField(value: phase.monthlyIncome) { v in var p = phase; p.monthlyIncome = v; onChange(p) }
                }
                Field(label: L("月支出")) {
                    NumberField(value: phase.monthlySpending) { v in var p = phase; p.monthlySpending = v; onChange(p) }
                }
            }
        }
        .padding(.bottom, 6)
    }
}

private struct SavedPlans: View {
    @Environment(AppModel.self) private var model
    let store: PlanStore
    let state: PlanState
    @State private var open = false
    @State private var name = ""
    @State private var saved: [SavedScenario] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            DisclosureRow(title: L("保存或载入计划"), open: $open)
            if open {
                KeelField(placeholder: L("情景名称，如：买房"), text: $name, width: nil)
                Button(L("保存")) {
                    Task {
                        guard let api = model.api else { return }
                        let plan = SavedScenario(name: name.trimmingCharacters(in: .whitespaces), scenario: state.scenario, model: state.model)
                        if (try? await api.saveScenario(plan)) != nil {
                            model.banner = L("已保存情景「{name}」", ["name": plan.name])
                            saved = (try? await api.scenarios()) ?? saved
                        }
                    }
                }
                .buttonStyle(.keelOutlineCompact)
                .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
                ForEach(saved) { plan in
                    Button("↺ " + plan.name) { store.update { $0 = PlanState(scenario: plan.scenario, model: plan.model) } }
                        .buttonStyle(.plain)
                        .font(.ui(13))
                        .foregroundStyle(Theme.ink)
                }
            }
        }
        .task(id: open) {
            if open, let api = model.api { saved = (try? await api.scenarios()) ?? [] }
        }
    }
}

/// The ledger's facts beside the inputs: Keel can't know whether an internship ends or a salary
/// starts, so the plan's income and spending are yours; these are what actually happened.
private struct FactsBox: View {
    let facts: PlanFacts

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("账本里的事实 · 过去 12 个月（到 {m}）", ["m": Format.month(facts.asOf)]))
                .font(.ui(12.5, .medium)).foregroundStyle(Theme.ink)
            line(L("收入"), facts.incomeYear, facts.incomeBy)
            line(L("支出"), facts.spendingYear, facts.spendingBy)
            Text(L("最近 3 个月平均每月：收入 {i}，支出 {e}。期末账本可投资资产 {a}。", [
                "i": m0(facts.recentIncome), "e": m0(facts.recentSpending), "a": m0(facts.investable),
            ]))
            .font(.ui(11.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            if facts.months < 12 {
                Text(L("账本里只有 {n} 个月的数据。", ["n": facts.months])).font(.ui(11.5)).foregroundStyle(Theme.muted)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.fill.opacity(0.5), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .padding(.bottom, 20)
    }

    private func line(_ title: String, _ total: Double, _ parts: [FactLine]) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title + " " + m0(total)).font(.mono(12)).foregroundStyle(Theme.ink)
            Text(parts.prefix(5).map { Format.account($0.category) + " " + m0($0.amount) }.joined(separator: " · "))
                .font(.ui(11)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
        }
    }
}
