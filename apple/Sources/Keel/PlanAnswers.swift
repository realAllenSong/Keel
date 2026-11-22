import KeelCore
import SwiftUI

// 规划, answer first: four questions people actually ask of a plan, each answered in one line
// before any chart, and the assumptions beside them in one place.

/// Calendar months as the plan uses them ("YYYY-MM").
enum PlanMonth {
    static var now: String { ReportingPeriod.month() }
    static func index(_ ym: String) -> Int {
        let p = ym.split(separator: "-").compactMap { Int($0) }
        return p.count == 2 ? p[0] * 12 + p[1] - 1 : 0
    }
    /// The calendar year `years` from now (fractional years allowed).
    static func year(after years: Double) -> Int { (index(now) + Int((years * 12).rounded())) / 12 }
}

/// What the cash bridge says, in the shapes the page needs.
struct PlanNumbers {
    let rows: [BridgeMonth]  // the next 24 months
    let all: [BridgeMonth]
    let low: BridgeMonth?
    let short: BridgeMonth?
    let next12: Double

    init(_ b: BridgeOut) {
        all = b.rows
        rows = Array(b.rows.prefix(24))
        low = rows.min { $0.balance < $1.balance }
        short = rows.first { $0.balance < 0 }
        next12 = b.rows.prefix(12).reduce(0) { $0 + $1.flow + $1.events }
    }
}

extension SimOut {
    func median(atYear k: Int) -> Double? { percentiles["p50"].flatMap { k < $0.count ? $0[k] : nil } }
    func low(atYear k: Int) -> Double? { percentiles["p25"].flatMap { k < $0.count ? $0[k] : nil } }
    /// First calendar year whose median reaches `amount`, if any within the horizon.
    func year(reaching amount: Double, percentile: String = "p50") -> Int? {
        guard let p = percentiles[percentile], let k = p.firstIndex(where: { $0 >= amount }) else { return nil }
        return PlanMonth.year(after: Double(k))
    }
    /// Financial independence: the third lighthouse (spending ÷ withdrawal rate), median year.
    var independenceYear: Int? {
        guard lighthouseMedianYear.count > 2, let y = lighthouseMedianYear[2] else { return nil }
        return PlanMonth.year(after: y)
    }
}

// MARK: - headline figures

struct PlanKPIs: View {
    let numbers: PlanNumbers?
    let sim: SimOut?

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            tile(L("美元现金最低点"),
                 numbers?.low.map { m0($0.balance) } ?? "—",
                 numbers?.low.map { Format.month($0.month) + " · " + (numbers?.short == nil ? L("不会断") : L("需要补")) } ?? L("计算中…"),
                 alert: numbers?.short != nil)
            divider
            tile(L("5 年后净资产"), sim?.median(atYear: 5).map(compactUSD) ?? "—", L("中位数 · 今天的美元"))
            divider
            tile(L("财务自由"), sim.map { $0.independenceYear.map(String.init) ?? L("还远") } ?? "—",
                 sim.flatMap { $0.lighthouseTargets.count > 2 ? L("需要 {x}", ["x": compactUSD($0.lighthouseTargets[2])]) : nil } ?? L("计算中…"))
            divider
            tile(L("未来 12 个月净存"), numbers.map { ($0.next12 >= 0 ? "+" : "") + compactUSD($0.next12) } ?? "—", L("收入 − 支出 ± 大事"))
        }
        .padding(.vertical, 16)
        .overlay(alignment: .top) { Hairline(color: Theme.rule) }
        .overlay(alignment: .bottom) { Hairline(color: Theme.rule) }
    }

    private var divider: some View { Rectangle().fill(Theme.border).frame(width: 1).padding(.horizontal, 16) }

    private func tile(_ title: String, _ value: String, _ sub: String, alert: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title.uppercased()).font(.ui(10.5)).tracking(1.4).foregroundStyle(Theme.muted)
            Text(value).font(.mono(26, .medium)).foregroundStyle(alert ? Theme.outflow : Theme.ink).lineLimit(1).minimumScaleFactor(0.6)
            Text(sub).font(.ui(11.5)).foregroundStyle(Theme.muted).lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - Q1 cash

struct CashQuestion: View {
    let bridge: BridgeOut
    let numbers: PlanNumbers
    let stages: Set<String>
    let drill: () -> Void

    var body: some View {
        Card(title: L("接下来 24 个月，美元够不够用？"), link: L("看逐月"), action: drill) {
            Text(answer).font(.ui(14.5)).foregroundStyle(numbers.short == nil ? Theme.ink2 : Theme.outflow).lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
            CashBars(rows: numbers.rows, low: numbers.low).frame(height: 168).padding(.top, 4)
            let big = numbers.rows.filter { $0.events != 0 }
            if !big.isEmpty {
                LazyVGrid(columns: [GridItem(.flexible(), alignment: .leading), GridItem(.flexible(), alignment: .leading)], alignment: .leading, spacing: 6) {
                    ForEach(big, id: \.month) { r in
                        HStack(spacing: 8) {
                            Text(Format.month(r.month)).font(.mono(11.5)).foregroundStyle(Theme.muted)
                            Text(r.labels.filter { !isStage($0) }.joined(separator: " · ")).font(.ui(12)).foregroundStyle(Theme.ink).lineLimit(1)
                            Text((r.events > 0 ? "+" : "") + compactUSD(r.events)).font(.mono(11.5)).foregroundStyle(r.events > 0 ? Theme.inflow : Theme.outflow)
                        }
                    }
                }
            }
            Text(L("起点：美元现金 {c} − 信用卡欠款 {d} = {s}", ["c": m0(bridge.cash), "d": m0(bridge.cards), "s": m0(bridge.start)]) + other)
                .font(.ui(11.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
        }
    }

    /// The bridge's labels mix stage names (every month) and event names (their month).
    private func isStage(_ label: String) -> Bool { stages.contains(label) }

    private var answer: String {
        if let short = numbers.short, let low = numbers.low {
            return L("不够：{m} 起美元现金变成负数，最低 {v}（{lm}）。提前换汇、安排家里支持，或推迟一笔支出。", [
                "m": Format.month(short.month), "v": m0(low.balance), "lm": Format.month(low.month),
            ])
        }
        guard let low = numbers.low else { return "" }
        return L("够。最紧的是 {m}，还剩 {v}。", ["m": Format.month(low.month), "v": m0(low.balance)])
    }

    private var other: String {
        let parts = bridge.other.sorted { $0.key < $1.key }.map { m0($0.value, currency: $0.key) }
        return parts.isEmpty ? "" : L("；另有 {x} 未计入", ["x": parts.joined(separator: " + ")])
    }
}

/// Month-end dollars as bars; the low point in the accent, below zero in the accent too.
struct CashBars: View {
    let rows: [BridgeMonth]
    let low: BridgeMonth?
    @State private var hover: Int?

    var body: some View {
        GeometryReader { g in
            let hi = max(1, rows.map(\.balance).max() ?? 1, 0)
            let lo = min(0, rows.map(\.balance).min() ?? 0)
            let plot = g.size.height - 20
            let zero = plot * CGFloat(hi / (hi - lo))
            let step = g.size.width / CGFloat(max(1, rows.count))
            ZStack(alignment: .topLeading) {
                Rectangle().fill(Theme.rule).frame(width: g.size.width, height: 1).offset(y: zero)
                ForEach(Array(rows.enumerated()), id: \.offset) { i, r in
                    let h = max(1, plot * CGFloat(abs(r.balance) / (hi - lo)))
                    RoundedRectangle(cornerRadius: 2)
                        .fill(r.month == low?.month || r.balance < 0 ? Theme.signal : (hover == i ? Theme.ink : Theme.line2))
                        .frame(width: max(2, step - 3), height: h)
                        .offset(x: CGFloat(i) * step, y: r.balance >= 0 ? zero - h : zero)
                    if i % 3 == 0 {
                        Text(String(r.month.dropFirst(2))).font(.mono(10)).foregroundStyle(Theme.muted)
                            .offset(x: CGFloat(i) * step, y: plot + 6)
                    }
                }
                if let i = hover, i < rows.count {
                    let r = rows[i]
                    Text("\(Format.month(r.month)) · \(m0(r.balance))").font(.mono(11)).foregroundStyle(Theme.card)
                        .padding(.horizontal, 7).padding(.vertical, 4)
                        .background(Theme.ink, in: RoundedRectangle(cornerRadius: 5))
                        .offset(x: min(CGFloat(i) * step, g.size.width - 150), y: -4)
                } else if let low, let i = rows.firstIndex(where: { $0.month == low.month }) {
                    Text(compactUSD(low.balance)).font(.mono(10.5)).foregroundStyle(Theme.signal)
                        .offset(x: CGFloat(i) * step, y: low.balance >= 0 ? zero - plot * CGFloat(low.balance / (hi - lo)) - 16 : zero + 4)
                }
            }
            .contentShape(Rectangle())
            .onContinuousHover { phase in
                if case .active(let p) = phase { hover = min(rows.count - 1, max(0, Int(p.x / step))) } else { hover = nil }
            }
        }
    }
}

// MARK: - Q2 milestones

struct MilestoneQuestion: View {
    let sim: SimOut

    var body: some View {
        Card(title: L("1 年、3 年、5 年后有多少？"), hint: L("净资产中位数 · 今天的美元 · 含投资收益")) {
            HStack(spacing: 12) {
                ForEach([1, 3, 5], id: \.self) { k in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(L("{n} 年后 · {y} 年", ["n": k, "y": PlanMonth.year(after: Double(k))])).font(.ui(11.5)).foregroundStyle(Theme.muted)
                        Text(sim.median(atYear: k).map(compactUSD) ?? "—").font(.mono(24, .medium)).foregroundStyle(Theme.ink)
                        Text(L("市场差时 {x}", ["x": sim.low(atYear: k).map(compactUSD) ?? "—"])).font(.ui(11.5)).foregroundStyle(Theme.muted)
                    }
                    .padding(14)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.paper.opacity(0.7), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                }
            }
            Text(L("「市场差时」是 25% 分位：四分之一的模拟路径比这更差。"))
                .font(.ui(11.5)).foregroundStyle(Theme.muted)
        }
    }
}

// MARK: - Q3 goals

struct GoalsQuestion: View {
    let sim: SimOut
    let goals: [PlanGoal]

    var body: some View {
        Card(title: L("目标什么时候能达到？"), hint: L("在右侧「目标」里添加")) {
            VStack(spacing: 0) {
                if sim.lighthouseTargets.count > 2, sim.lighthouseTargets[2] > 0 {
                    row(L("财务自由"), L("资产够按提款率覆盖退休开销 · {x}", ["x": compactUSD(sim.lighthouseTargets[2])]),
                        sim.independenceYear, sim.year(reaching: sim.lighthouseTargets[2], percentile: "p25"))
                }
                ForEach(Array(goals.enumerated()), id: \.offset) { _, g in
                    Hairline(color: Theme.border.opacity(0.6))
                    row(g.label.isEmpty ? L("目标") : g.label, compactUSD(g.amount), sim.year(reaching: g.amount), sim.year(reaching: g.amount, percentile: "p25"))
                }
            }
        }
    }

    private func row(_ title: String, _ sub: String, _ median: Int?, _ bad: Int?) -> some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.ui(13.5)).foregroundStyle(Theme.ink)
                Text(sub).font(.ui(11.5)).foregroundStyle(Theme.muted)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 3) {
                Text(median.map { L("约 {y} 年", ["y": $0]) } ?? L("模拟期内达不到")).font(.mono(13.5)).foregroundStyle(median == nil ? Theme.outflow : Theme.ink)
                if let bad, bad != median { Text(L("市场差时 {y} 年", ["y": bad])).font(.ui(11)).foregroundStyle(Theme.muted) }
            }
        }
        .padding(.vertical, 11)
    }
}

// MARK: - Q4 compare

/// Your plan against one saved alternative ("住 LIC", "年终奖晚一年"), on the numbers above.
struct CompareQuestion: View {
    @Environment(AppModel.self) private var model
    let store: PlanStore
    let state: PlanState
    let numbers: PlanNumbers?
    @State private var saved: [SavedScenario] = []
    @State private var pick: String?
    @State private var other: (sim: SimOut, numbers: PlanNumbers)?
    @State private var name = ""
    @State private var loading = false

    var body: some View {
        Card(title: L("这个决定划不划算？"), hint: L("和一个存下来的情景比")) {
            if saved.isEmpty {
                Text(L("先把当前计划存为一个情景（例如「住 Jersey」），在右侧改几个数，再存一个（「住 LIC」）。这里就会把两者并排比较。"))
                    .font(.ui(13)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            } else {
                HStack(spacing: 8) {
                    Text(L("当前计划 对比")).font(.ui(12.5)).foregroundStyle(Theme.muted)
                    Menu(pick ?? L("选一个情景")) {
                        ForEach(saved) { s in Button(s.name) { pick = s.name } }
                    }
                    .menuStyle(.borderlessButton).fixedSize()
                    if loading { ProgressView().controlSize(.small) }
                }
                if let other, let sim = store.sim, let numbers { table(sim, numbers, other.sim, other.numbers) }
            }
            HStack(spacing: 8) {
                KeelField(placeholder: L("情景名称，如：住 LIC"), text: $name, width: 200)
                Button(L("把当前计划存为情景")) { Task { await save() } }
                    .buttonStyle(.keelOutlineCompact)
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            .padding(.top, 4)
        }
        .task { if let api = model.api { saved = (try? await api.scenarios()) ?? [] } }
        .task(id: CompareKey(pick: pick, scenario: state.scenario)) { await compare() }
    }

    private func table(_ a: SimOut, _ an: PlanNumbers, _ b: SimOut, _ bn: PlanNumbers) -> some View {
        let rows: [(String, Double?, Double?, Bool)] = [
            (L("美元现金最低点"), an.low?.balance, bn.low?.balance, true),
            (L("未来 12 个月净存"), an.next12, bn.next12, true),
            (L("5 年后净资产（中位）"), a.median(atYear: 5), b.median(atYear: 5), true),
            (L("10 年后净资产（中位）"), a.median(atYear: 10), b.median(atYear: 10), true),
            (L("财务自由（年）"), a.independenceYear.map(Double.init), b.independenceYear.map(Double.init), false),
        ]
        return VStack(spacing: 0) {
            HStack {
                Text("").frame(maxWidth: .infinity, alignment: .leading)
                Text(L("当前计划")).frame(width: 110, alignment: .trailing)
                Text(pick ?? "").lineLimit(1).frame(width: 110, alignment: .trailing)
                Text(L("差别")).frame(width: 90, alignment: .trailing)
            }
            .font(.ui(11)).foregroundStyle(Theme.muted).padding(.vertical, 6)
            Hairline(color: Theme.rule)
            ForEach(Array(rows.enumerated()), id: \.offset) { _, r in
                let (title, x, y, money) = r
                let d = (x != nil && y != nil) ? y! - x! : nil
                HStack {
                    Text(title).font(.ui(13)).foregroundStyle(Theme.ink).frame(maxWidth: .infinity, alignment: .leading)
                    Text(fmt(x, money)).frame(width: 110, alignment: .trailing)
                    Text(fmt(y, money)).frame(width: 110, alignment: .trailing)
                    Text(d.map { ($0 > 0 ? "+" : "") + (money ? compactUSD($0) : String(Int($0))) } ?? "—")
                        .foregroundStyle(d.map { ($0 > 0) == money ? Theme.inflow : ($0 == 0 ? Theme.muted : Theme.outflow) } ?? Theme.muted)
                        .frame(width: 90, alignment: .trailing)
                }
                .font(.mono(12.5)).foregroundStyle(Theme.ink).padding(.vertical, 9)
                Hairline(color: Theme.border.opacity(0.6))
            }
            Text(L("差别 = 情景 − 当前计划。年份越早越好。")).font(.ui(11)).foregroundStyle(Theme.muted).padding(.top, 6)
        }
    }

    private struct CompareKey: Equatable { let pick: String?; let scenario: Scenario }

    private func fmt(_ v: Double?, _ money: Bool) -> String {
        guard let v else { return "—" }
        return money ? compactUSD(v) : String(Int(v))
    }

    private func compare() async {
        guard let api = model.api, let pick, let s = saved.first(where: { $0.name == pick }) else { other = nil; return }
        loading = true
        defer { loading = false }
        let st: PlanState = {
            var st = PlanState(scenario: s.scenario, model: s.model)
            st.cashflowReviewed = true
            return st
        }()
        async let sim = api.simulate(st, paths: 3000)
        async let br = api.bridge(st, months: 24)
        if let sim = try? await sim, let br = try? await br, !Task.isCancelled { other = (sim, PlanNumbers(br)) }
    }

    private func save() async {
        guard let api = model.api else { return }
        let plan = SavedScenario(name: name.trimmingCharacters(in: .whitespaces), scenario: state.scenario, model: state.model)
        if (try? await api.saveScenario(plan)) != nil {
            model.banner = L("已保存情景「{name}」", ["name": plan.name])
            saved = (try? await api.scenarios()) ?? saved
            name = ""
        }
    }
}

// MARK: - month by month

/// The drill-down behind "看逐月": every month (or year) of the plan as a row.
struct BridgeTableSheet: View {
    let bridge: BridgeOut
    let phases: [PlanPhase]
    @Environment(\.dismiss) private var dismiss
    @State private var mode = "month"

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(L("逐月推演")).font(.display(21)).foregroundStyle(Theme.ink)
                    Text(L("美元现金 · 不含投资收益 · 阶段收支 + 大事")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                }
                Spacer()
                Segmented(items: [("month", L("按月 · 24 个月")), ("year", L("按年 · 10 年"))], selection: $mode)
            }
            ScrollView {
                VStack(spacing: 0) {
                    header
                    Hairline(color: Theme.rule)
                    if mode == "month" {
                        ForEach(Array(bridge.rows.prefix(24).enumerated()), id: \.offset) { _, r in
                            line(Format.month(r.month), stage(r.month), r.income ?? max(0, r.flow), r.spending ?? max(0, -r.flow), r.events,
                                 r.labels.filter { $0 != stage(r.month) }.joined(separator: "、"), r.balance)
                        }
                    } else {
                        ForEach(years, id: \.year) { y in
                            line(String(y.year), y.stage, y.income, y.spending, y.events, y.notes, y.balance)
                        }
                    }
                }
            }
            HStack {
                Text(mode == "month" ? L("最右一列是月末美元现金。") : L("最右一列是年末美元现金；长期的增长看「1 年、3 年、5 年后有多少」。"))
                    .font(.ui(11.5)).foregroundStyle(Theme.muted)
                Spacer()
                Button(L("关闭")) { dismiss() }.buttonStyle(.keelOutlineCompact).keyboardShortcut(.cancelAction)
            }
        }
        .padding(24)
        .frame(width: 900, height: 660)
        .background(Theme.card)
    }

    private var header: some View {
        HStack(spacing: 0) {
            cell(mode == "month" ? L("月份") : L("年"), 76, .leading)
            cell(L("阶段"), 170, .leading)
            cell(L("收入"), 90)
            cell(L("支出"), 90)
            cell(L("大事"), 90)
            cell(L("是什么"), nil, .leading).padding(.leading, 16)
            cell(L("美元现金"), 100)
        }
        .font(.ui(11)).foregroundStyle(Theme.muted).padding(.vertical, 8)
    }

    private func line(_ when: String, _ stage: String, _ income: Double, _ spending: Double, _ events: Double, _ notes: String, _ balance: Double) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                cell(when, 76, .leading).font(.mono(12.5)).foregroundStyle(Theme.ink)
                cell(stage, 170, .leading).font(.ui(12.5)).foregroundStyle(Theme.ink2)
                cell(income == 0 ? "—" : "+" + m0(income), 90).font(.mono(12.5)).foregroundStyle(income == 0 ? Theme.muted : Theme.inflow)
                cell(spending == 0 ? "—" : "−" + m0(spending), 90).font(.mono(12.5)).foregroundStyle(Theme.ink)
                cell(events == 0 ? "" : (events > 0 ? "+" : "") + m0(events), 90).font(.mono(12.5)).foregroundStyle(events > 0 ? Theme.inflow : Theme.outflow)
                cell(notes, nil, .leading).font(.ui(12)).foregroundStyle(Theme.muted).lineLimit(1).padding(.leading, 16)
                cell(m0(balance), 100).font(.mono(12.5, .medium)).foregroundStyle(balance < 0 ? Theme.outflow : Theme.ink)
            }
            .padding(.vertical, 8)
            Hairline(color: Theme.border.opacity(0.6))
        }
    }

    @ViewBuilder private func cell(_ s: String, _ width: CGFloat?, _ align: Alignment = .trailing) -> some View {
        if let width { Text(s).frame(width: width, alignment: align) } else { Text(s).frame(maxWidth: .infinity, alignment: align) }
    }

    private func stage(_ month: String) -> String {
        let i = PlanMonth.index(month)
        return phases.last(where: { PlanMonth.index($0.start) <= i && i <= PlanMonth.index($0.end) })?.label ?? L("长期预期")
    }

    private struct YearRow { let year: Int; let stage: String; let income, spending, events, balance: Double; let notes: String }

    private var years: [YearRow] {
        let grouped = Dictionary(grouping: bridge.rows) { Int($0.month.prefix(4)) ?? 0 }
        return grouped.keys.sorted().map { y in
            let rows = grouped[y] ?? []
            var stages: [String] = []
            for r in rows where !stages.contains(stage(r.month)) { stages.append(stage(r.month)) }
            let notes = rows.flatMap { r in r.labels.filter { $0 != stage(r.month) && r.events != 0 } }
            return YearRow(
                year: y, stage: stages.joined(separator: " → "),
                income: rows.reduce(0) { $0 + ($1.income ?? max(0, $1.flow)) },
                spending: rows.reduce(0) { $0 + ($1.spending ?? max(0, -$1.flow)) },
                events: rows.reduce(0) { $0 + $1.events },
                balance: rows.last?.balance ?? 0,
                notes: Array(NSOrderedSet(array: notes)).compactMap { $0 as? String }.joined(separator: "、")
            )
        }
    }
}

// MARK: - assumptions

/// Every assumption in one column: where you start, the stages, the big one-offs and the goals.
/// Rows stay folded to one line; the one you open edits in place.
struct PlanInspector: View {
    let store: PlanStore
    let state: PlanState
    @State private var openStage: Int?
    @State private var openEvent: Int?
    @State private var more = false

    private var s: Scenario { state.scenario }
    private func up(_ change: (inout Scenario) -> Void) { store.update { change(&$0.scenario) } }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text(L("你的假设")).font(.display(17, .medium)).foregroundStyle(Theme.ink)
                Spacer()
                Text(L("改这里，左边立刻重算")).font(.ui(11)).foregroundStyle(Theme.muted)
            }
            start
            stagesList
            eventsList
            goalsList
            Hairline().padding(.vertical, 16)
            DisclosureRow(title: L("长期收支、投资与更多假设"), open: $more)
            if more { PlanForm(store: store, state: state, embedded: true).padding(.top, 8) }
        }
        .padding(16)
        .surface()
    }

    private var stagesList: some View {
        VStack(alignment: .leading, spacing: 0) {
            section(L("阶段 · 每月"))
            ForEach(Array((s.phases ?? []).enumerated()), id: \.offset) { i, p in
                row(open: openStage == i, toggle: { openStage = openStage == i ? nil : i; openEvent = nil },
                    dot: Theme.series[(i + 1) % Theme.series.count], title: p.label,
                    value: net(p.monthlyIncome - p.monthlySpending) + L("/月"), sub: p.start == p.end ? p.start : p.start + " → " + p.end) {
                    PhaseEditor(phase: p) { changed in
                        up { sc in
                            var l = sc.phases ?? []
                            guard i < l.count else { return }
                            if let changed { l[i] = changed } else { l.remove(at: i); openStage = nil }
                            sc.phases = l
                        }
                    }
                }
            }
            LinkButton(title: L("＋ 添加阶段"), arrow: false) {
                let list = s.phases ?? []
                let start = list.last.map { PlanForm.nextMonth($0.end) } ?? PlanMonth.now
                up { $0.phases = list + [PlanPhase(label: L("新阶段"), start: start, end: PlanForm.nextMonth(start, by: 11), monthlyIncome: 0, monthlySpending: 0)] }
                openStage = list.count
            }
            .padding(.top, 8)
            Text(L("阶段之外按「长期收支」计算。")).font(.ui(11)).foregroundStyle(Theme.muted).padding(.top, 4)

        }
    }

    private var eventsList: some View {
        VStack(alignment: .leading, spacing: 0) {
            section(L("大事 · 一次性"))
            ForEach(Array((s.events ?? []).enumerated()), id: \.offset) { i, e in
                row(open: openEvent == i, toggle: { openEvent = openEvent == i ? nil : i; openStage = nil },
                    dot: nil, title: (e.label ?? "").isEmpty ? L("事件") : e.label!,
                    value: net(e.amount), sub: e.date ?? L("第 {n} 年", ["n": NumberField.format(e.year)])) {
                    EventEditor(event: e) { changed in
                        up { sc in
                            var l = sc.events ?? []
                            guard i < l.count else { return }
                            if let changed { l[i] = changed } else { l.remove(at: i); openEvent = nil }
                            sc.events = l
                        }
                    }
                }
            }
            LinkButton(title: L("＋ 添加大事"), arrow: false) {
                up { $0.events = ($0.events ?? []) + [LifeEvent(label: L("人生大事"), year: 0, amount: 0, date: PlanForm.nextMonth(PlanMonth.now))] }
                openEvent = (s.events ?? []).count
            }
            .padding(.top, 8)

        }
    }

    private var goalsList: some View {
        VStack(alignment: .leading, spacing: 0) {
            section(L("目标 · 今天的美元"))
            ForEach(Array((state.goals ?? []).enumerated()), id: \.offset) { i, g in
                HStack(spacing: 8) {
                    TextInput(text: g.label) { v in store.update { st in if i < (st.goals ?? []).count { st.goals![i].label = v } } }
                    NumberField(value: g.amount, width: 90) { v in store.update { st in if i < (st.goals ?? []).count { st.goals![i].amount = v } } }
                        .frame(width: 96)
                    Button { store.update { st in if i < (st.goals ?? []).count { st.goals!.remove(at: i) } } } label: {
                        Image(systemName: "xmark").font(.ui(10, .semibold)).foregroundStyle(Theme.muted).frame(width: 16, height: 28)
                    }
                    .buttonStyle(.plain).help(L("删除目标"))
                }
                .padding(.top, 8)
            }
            LinkButton(title: L("＋ 添加目标"), arrow: false) {
                store.update { $0.goals = ($0.goals ?? []) + [PlanGoal(label: L("新目标"), amount: 100_000)] }
            }
            .padding(.top, 8)
            Text(L("财务自由会自动算，不用另加。")).font(.ui(11)).foregroundStyle(Theme.muted).padding(.top, 4)

        }
    }

    /// Where the plan starts, and a nudge when the ledger has moved on since it was set.
    private var start: some View {
        let ledger = store.defaults?.facts?.investable
        return VStack(alignment: .leading, spacing: 6) {
            section(L("起点"))
            HStack {
                Text(L("可投资资产")).font(.ui(12.5)).foregroundStyle(Theme.ink)
                Spacer()
                NumberField(value: s.initial, width: 110) { v in up { $0.initial = v } }.frame(width: 116)
            }
            if let ledger, abs(ledger - s.initial) > max(500, 0.02 * ledger) {
                LinkButton(title: L("改用账本最新值 {x}", ["x": m0(ledger)]), arrow: false) { up { $0.initial = ledger } }
            }
        }
    }

    private func section(_ title: String) -> some View {
        Text(title.uppercased()).font(.ui(10.5)).tracking(1.4).foregroundStyle(Theme.muted).padding(.top, 18).padding(.bottom, 4)
    }

    private func net(_ v: Double) -> String { (v > 0 ? "+" : v < 0 ? "−" : "") + compactUSD(abs(v)) }

    private func row<Editor: View>(open: Bool, toggle: @escaping () -> Void, dot: Color?, title: String, value: String, sub: String,
                                   @ViewBuilder editor: () -> Editor) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: { withAnimation(.easeOut(duration: 0.15)) { toggle() } }) {
                HStack(spacing: 8) {
                    if let dot { Dot(color: dot) }
                    VStack(alignment: .leading, spacing: 2) {
                        Text(title).font(.ui(12.5)).foregroundStyle(Theme.ink).lineLimit(1)
                        Text(sub).font(.mono(10.5)).foregroundStyle(Theme.muted)
                    }
                    Spacer()
                    Text(value).font(.mono(12)).foregroundStyle(Theme.muted)
                    Image(systemName: open ? "chevron.down" : "chevron.right").font(.ui(9, .semibold)).foregroundStyle(Theme.muted)
                }
                .padding(.vertical, 8)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open { editor().padding(.bottom, 10) }
            Hairline(color: Theme.border.opacity(0.6))
        }
    }
}
