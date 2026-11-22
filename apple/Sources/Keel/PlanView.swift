import Charts
import KeelCore
import SwiftUI

/// 规划: one plan, prefilled from the ledger, and what it means for retirement, risk and the three
/// lighthouses. Every edit re-runs the simulations (debounced); results stay on screen meanwhile.
struct PlanView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        switch model.planTool {
        case .workspace: PlanWorkspace()
        case .backtest: BacktestView()
        case .housing: HousingView()
        }
    }
}

/// The plan lives on this Mac (UserDefaults); "恢复账本数据" goes back to the ledger's numbers.
@MainActor
@Observable
final class PlanStore {
    static let key = "keel.plan.state.v1"
    static let shared = PlanStore()
    var state: PlanState?
    // The last results, kept while you visit other places.
    var sim: SimOut?
    var drawdown: SimOut?
    var retire: RetirementOut?
    var runs = 0  // bumps when new results land, so the lazily loaded retirement year refreshes
    var replays: [ReplayOut]?
    var bridge: BridgeOut?
    var simError: String?
    var computed: PlanState?
    var defaults: SimDefaults?
    var error: String?
    var isCustom: Bool { UserDefaults.standard.data(forKey: Self.key) != nil }

    func load(_ api: API) async {
        do {
            let d = try await api.simDefaults()
            defaults = d
            error = nil
            if let data = UserDefaults.standard.data(forKey: Self.key), var saved = try? JSONDecoder().decode(PlanState.self, from: data) {
                // A plan with no stages (older, or all deleted) picks up the stages you wrote down.
                if (saved.scenario.phases ?? []).isEmpty, let seed = d.scenario.phases, !seed.isEmpty {
                    saved.scenario.phases = seed
                    let have = Set((saved.scenario.events ?? []).compactMap(\.label))
                    saved.scenario.events = (saved.scenario.events ?? []) + (d.scenario.events ?? []).filter { $0.date != nil && !have.contains($0.label ?? "") }
                    if let data = try? JSONEncoder().encode(saved) { UserDefaults.standard.set(data, forKey: Self.key) }
                }
                state = saved
            } else {
                state = PlanState(scenario: d.scenario, model: d.model)
                state?.cashflowReviewed = false
            }
        } catch {
            self.error = error.localizedDescription
        }
    }

    func update(_ change: (inout PlanState) -> Void) {
        guard var s = state else { return }
        change(&s)
        state = s
        if let data = try? JSONEncoder().encode(s) { UserDefaults.standard.set(data, forKey: Self.key) }
    }

    func reset() {
        UserDefaults.standard.removeObject(forKey: Self.key)
        if let d = defaults { state = PlanState(scenario: d.scenario, model: d.model); state?.cashflowReviewed = false }
    }
}

private struct PlanWorkspace: View {
    @Environment(AppModel.self) private var model
    // Shared, so leaving 规划 and coming back shows the last results instead of recomputing.
    @State private var store = PlanStore.shared
    @State private var computing = false
    @State private var drill = false
    @State private var risk = false
    @State private var investing = false

    var body: some View {
        Page {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(L("规划 · 截至 {d}", ["d": Format.day(model.today)]).uppercased()).font(.ui(10.5)).tracking(1.4).foregroundStyle(Theme.muted)
                    Text(L("接下来会怎样")).font(.display(32)).foregroundStyle(Theme.ink)
                }
                if let state = store.state {
                    if state.projectionReady {
                        PlanKPIs(numbers: store.bridge.map(PlanNumbers.init), sim: store.sim)
                            .opacity(stale && store.sim != nil ? 0.6 : 1)
                    }
                    HStack(alignment: .top, spacing: 24) {
                        Group {
                            if state.projectionReady { answers(state) } else { gate(state) }
                        }
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                        PlanInspector(store: store, state: state).frame(width: 330)
                    }
                    GoalsSection()
                    HStack(spacing: 16) {
                        ToolLink(title: L("回测：定投 vs 等跌了再买")) { model.planTool = .backtest }
                        ToolLink(title: L("买房 / 租房 / 贷款计算器")) { model.planTool = .housing }
                    }
                } else if let error = store.error {
                    ErrorLine(message: L("暂时读不到账本：{e}", ["e": error]))
                } else {
                    SkeletonRows(count: 6)
                }
            }
        }
        .task { if let api = model.api, store.state == nil { await store.load(api) } }
        .task(id: store.state) { await run() }
        .sheet(isPresented: $drill) {
            if let bridge = store.bridge { BridgeTableSheet(bridge: bridge, phases: store.state?.scenario.phases ?? []) }
        }
    }

    private var stale: Bool { computing || store.computed != store.state }

    private func gate(_ state: PlanState) -> AnyView {
        let retirement = state.scenario.hasRetirementAccount && state.retirementReviewed != true
        if retirement {
            return AnyView(Card(title: L("先确认退休账户假设")) {
                Text(L("在右侧「长期收支、投资与更多假设」里填写退休资金可提取的时间和预计有效税率。"))
                    .font(.ui(14)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            })
        }
        // A new plan starts from three plain questions, prefilled from the ledger where it can.
        let s = state.scenario
        return AnyView(Card(title: L("先回答三个问题")) {
            Text(L("Keel 按你的账本先填好了，改成你接下来的打算就行。以后随时可以在右边改。"))
                .font(.ui(13.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 12) {
                question(L("现在能拿去投资的钱有多少？"), hint: L("存款和投资账户，减掉要还的欠款和应急备用金")) {
                    NumberField(value: s.initial, width: 160) { v in store.update { $0.scenario.initial = v ?? 0 } }
                }
                question(L("一年税后收入大概多少？"), hint: L("工资到手，加上其他稳定收入")) {
                    NumberField(value: s.annualIncome, width: 160) { v in store.update { $0.scenario.annualIncome = v ?? 0 } }
                }
                question(L("一年大概花多少？"), hint: L("房租、吃饭、交通、日常，按今天的价格")) {
                    NumberField(value: s.annualSpending, width: 160) { v in store.update { $0.scenario.annualSpending = v ?? 0 } }
                }
            }
            .padding(.top, 4)
            Button(L("开始计算")) { store.update { $0.cashflowReviewed = true } }.buttonStyle(.keelPrimary).padding(.top, 6)
        })
    }

    private func question<Content: View>(_ title: String, hint: String, @ViewBuilder field: () -> Content) -> some View {
        HStack(alignment: .center, spacing: 16) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.ui(14, .medium)).foregroundStyle(Theme.ink)
                Text(hint).font(.ui(11.5)).foregroundStyle(Theme.muted)
            }
            Spacer(minLength: 12)
            field()
            Text("USD").font(.mono(11.5)).foregroundStyle(Theme.muted)
        }
    }

    private func answers(_ state: PlanState) -> some View {
        VStack(alignment: .leading, spacing: 20) {
            if let error = store.simError {
                ErrorLine(message: L("暂时算不出来：{e}", ["e": error]))
            }
            if let bridge = store.bridge {
                CashQuestion(bridge: bridge, numbers: PlanNumbers(bridge), stages: Set((state.scenario.phases ?? []).map(\.label))) { drill = true }
            } else {
                Card(title: L("接下来 24 个月，美元够不够用？")) { Computing() }
            }
            if let sim = store.sim {
                MilestoneQuestion(sim: sim)
                GoalsQuestion(sim: sim, goals: state.goals ?? [])
            } else {
                Card(title: L("1 年、3 年、5 年后有多少？")) { Computing() }
            }
            CompareQuestion(store: store, state: state, numbers: store.bridge.map(PlanNumbers.init))
            VStack(alignment: .leading, spacing: 0) {
                Hairline()
                DisclosureRow(title: L("市场风险：模拟路径、资金不足与回撤"), open: $risk).padding(.vertical, 6)
                if risk {
                    riskDetail(state).padding(.bottom, 16)
                        .task(id: store.runs) { await loadRetirement() }
                }
                Hairline()
                DisclosureRow(title: L("投资配置、灯塔与敏感度"), open: $investing).padding(.vertical, 6)
                if investing {
                    VStack(alignment: .leading, spacing: 20) {
                        AllocationCard(store: store, state: state)
                        LighthouseCard(sim: store.sim)
                        SensitivityCard(state: state)
                    }
                    .padding(.bottom, 16)
                }
                Hairline()
            }
        }
        .opacity(stale && store.sim != nil ? 0.6 : 1)
        .animation(.easeOut(duration: 0.2).delay(0.3), value: stale)
    }

    private func riskDetail(_ state: PlanState) -> some View {
        VStack(alignment: .leading, spacing: 20) {
            Headline(sim: store.sim, retire: store.retire, stale: stale, error: nil, workYears: Int(state.scenario.workYears))
            Card(title: L("可投资资产路径")) {
                Text(L("深色带为中间 50%，浅色带为 90%，线为中位数。金额均为扣除通胀后的今天的美元。")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                if let sim = store.sim {
                    FanChart(sim: sim, retireYear: Int(state.scenario.workYears), events: state.scenario.events ?? []).frame(height: 320)
                    Hairline().padding(.top, 6)
                    Text(L("累计资金不足路径占比")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                    RuinChart(sim: sim).frame(height: 140)
                } else {
                    Computing().frame(minHeight: 400, alignment: .topLeading)
                }
            }
            Text(L("这些比例仅来自当前假设下的历史市场抽样，不代表未来保证。收入、开销和工作年限按输入持续；未单独模拟失业、未来税制或应税账户投资税。退休账户按输入时点和有效税率提取，图中含税延账户的税前余额。买房、换车事件会减少投资资金，图中不另计房产和车辆净值。"))
                .font(.ui(11.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            DrawdownCard(drawdown: store.drawdown, replays: store.replays)
        }
    }

    private func run() async {
        guard let state = store.state, state.projectionReady, let api = model.api else { return }
        if store.computed == state, store.sim != nil { return }  // already showing these results
        try? await Task.sleep(for: .milliseconds(350))  // sliders: wait until the hand stops
        if Task.isCancelled { return }
        computing = true
        defer { computing = false }
        do {
            async let main = api.simulate(state)
            async let dd = api.simulate(state, horizon: 10)
            async let rp = api.replays(state)
            async let br = api.bridge(state, months: 120)
            // The cash answer is instant and comes first; the simulation fills in behind it.
            store.bridge = try? await br
            store.sim = try await main
            store.simError = nil
            store.drawdown = try? await dd
            store.replays = try? await rp
            if Task.isCancelled { return }
            store.computed = state
            store.retire = nil  // the earliest retirement year loads when 市场风险 is open
            store.runs += 1
        } catch is CancellationError {
        } catch {
            if !Task.isCancelled { store.simError = error.localizedDescription }
        }
    }
}

extension PlanWorkspace {
    /// The earliest year to stop working: only shown under 市场风险, so only computed there.
    func loadRetirement() async {
        guard store.retire == nil, let state = store.computed, let api = model.api else { return }
        store.retire = try? await api.retirement(state)
    }
}

private struct Computing: View {
    var body: some View { Text(L("计算中…")).font(.ui(13)).foregroundStyle(Theme.muted).padding(.vertical, 8) }
}

private struct ToolLink: View {
    let title: String
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            Text(title + " →").font(.ui(13.5)).foregroundStyle(Theme.ink)
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(hover ? Theme.fill : Theme.card, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Theme.border))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
    }
}

// MARK: - headline

private struct Headline: View {
    let sim: SimOut?
    let retire: RetirementOut?
    let stale: Bool
    let error: String?
    let workYears: Int

    var body: some View {
        let year = Calendar.current.component(.year, from: Date())
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text(L("资金覆盖路径占比")).font(.ui(13)).foregroundStyle(Theme.muted)
                Spacer()
                Text(sim == nil ? L("计算中…") : stale ? L("更新中…") : L("{n} 条路径 · {y} 年", ["n": sim!.paths.formatted(), "y": sim!.years.count - 1]))
                    .font(.ui(11.5)).foregroundStyle(Theme.muted)
            }
            HStack(alignment: .bottom, spacing: 20) {
                Text(sim.map { pct1($0.success) } ?? "—").font(.display(60)).foregroundStyle(Theme.signalInk)
                    .frame(maxWidth: .infinity, alignment: .leading)
                VStack(alignment: .leading, spacing: 6) {
                    Text(L("满足假设的退休参考年份")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                    Text(retire.map { r in r.earliest.map { L("{y} 年", ["y": year + $0]) } ?? L("40 年内未达标") } ?? L("计算中…"))
                        .font(.display(26)).foregroundStyle(Theme.ink)
                    Text((retire?.earliest.map { L("再工作 {n} 年 · ", ["n": $0]) } ?? "") + L("以资金覆盖路径占比 ≥ 95% 为目标"))
                        .font(.ui(11.5)).foregroundStyle(Theme.muted)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            Text(sim.map { sentence($0) } ?? L("正在用这份计划推算未来。")).font(.ui(14)).foregroundStyle(Theme.ink).lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
            if let error {
                Text(L("暂时算不出来：{e}", ["e": error]))
                    .font(.ui(13)).foregroundStyle(Theme.outflow)
            }
        }
        .padding(24)
        .background(Theme.card)
        .overlay(alignment: .top) { Rectangle().fill(Theme.signal).frame(height: 3) }
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Theme.border))
    }

    /// Lead with what happens, in plain words; a bare "0.0%" says nothing about the outcome.
    private func sentence(_ r: SimOut) -> String {
        let horizon = r.years.count - 1
        if r.success >= 0.5 {
            return L("按当前假设再工作 {w} 年，{h} 年模拟中有 {p}% 的路径资金够用，期末投资资产中位数 {m}。", [
                "w": workYears, "h": horizon, "p": Int((r.success * 100).rounded()), "m": m0(r.terminalMedian),
            ])
        }
        let runOut = r.ruinByYear.firstIndex { $0 >= 0.5 } ?? horizon
        return L("按当前假设，至少一半模拟路径在第 {n} 年出现资金不足", ["n": runOut])
            + (r.success > 0 ? L("（{p}% 的路径覆盖全部 {h} 年）", ["p": Int((r.success * 100).rounded()), "h": horizon]) : "")
            + L("。试试多工作几年、少花一点，或调整配置。")
    }
}

func pct1(_ v: Double) -> String { String(format: "%.1f%%", v * 100) }

// MARK: - charts

/// Wealth fan: 5–95 band, 25–75 band, median line; retirement and life events as dashed rules.
struct FanChart: View {
    let sim: SimOut
    let retireYear: Int
    let events: [LifeEvent]

    var body: some View {
        let p = sim.percentiles
        let years = sim.years.map { L("第 {n} 年", ["n": $0]) }
        let index = { (y: Int) in sim.years.firstIndex(of: y) ?? y }
        var marks = retireYear > 0 && retireYear < sim.years.count ? [ExhibitMark(index: index(retireYear), label: L("退休"))] : []
        for e in events {
            let y = Int((e.date.map(Self.yearsFromNow) ?? e.year).rounded())
            guard y < sim.years.count else { continue }
            marks.append(ExhibitMark(index: index(y), label: (e.label ?? "").isEmpty ? L("事件") : e.label!))
        }
        return LineExhibitChart(
            xLabels: years,
            series: [ExhibitSeries(label: L("中位数"), values: p["p50"] ?? [])],
            bands: [
                ExhibitBand(label: L("90% 的路径"), low: p["p5"] ?? [], high: p["p95"] ?? [], color: Theme.band),
                ExhibitBand(label: L("中间一半"), low: p["p25"] ?? [], high: p["p75"] ?? [], color: Theme.line2.opacity(0.45)),
            ],
            marks: marks,
            includeZero: true,
            height: 320,
            rightMargin: 170
        )
    }
}

extension FanChart {
    static func yearsFromNow(_ ym: String) -> Double {
        let a = ym.split(separator: "-").compactMap { Int($0) }, b = ReportingPeriod.month().split(separator: "-").compactMap { Int($0) }
        guard a.count == 2, b.count == 2 else { return 0 }
        return max(0, Double((a[0] - b[0]) * 12 + a[1] - b[1]) / 12)
    }
}

struct RuinChart: View {
    let sim: SimOut

    var body: some View {
        LineExhibitChart(
            xLabels: sim.years.map { L("第 {n} 年", ["n": $0]) },
            series: [ExhibitSeries(label: L("资金不足路径占比"), values: sim.ruinByYear, color: Theme.signal)],
            format: { Format.percent($0) },
            axis: { v, _ in Format.percent(v) },
            includeZero: true,
            height: 150,
            rightMargin: 170
        )
    }
}

// MARK: - result cards

private struct DrawdownCard: View {
    let drawdown: SimOut?
    let replays: [ReplayOut]?

    static var starts: [String: String] {
        ["1929-09": L("1929 大萧条"), "2000-01": L("2000 互联网泡沫"), "2008-01": L("2008 金融危机")]
    }

    var body: some View {
        Card(title: L("回撤风险"), hint: L("当前配置 · 未来 10 年 · 投资本身的波动，不含收支")) {
            if let dd = drawdown {
                HStack(alignment: .top) {
                    ForEach([20, 30, 40], id: \.self) { t in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(L("回撤 ≥ {p}%", ["p": t])).font(.ui(11.5)).foregroundStyle(Theme.muted)
                            Text(pct1(dd.mddExceed[String(t)] ?? 0)).font(.mono(22)).foregroundStyle(Theme.ink)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .padding(.top, 4)
                (Text(L("最长被套（中位）") + " ").foregroundColor(Theme.muted)
                    + Text(L("{n} 个月", ["n": Int(dd.underwaterMonthsMedian.rounded())])).font(.mono(13)).foregroundColor(Theme.ink))
                    .font(.ui(13))
            } else {
                Computing()
            }
            Hairline().padding(.top, 8)
            Text(L("历史最差起点")).font(.ui(13.5)).foregroundStyle(Theme.ink).padding(.top, 4)
            if let replays {
                VStack(spacing: 10) {
                    ForEach(replays.filter { Self.starts[$0.start] != nil }) { r in
                        HStack {
                            Text(Self.starts[r.start] ?? r.start).font(.ui(12.5)).foregroundStyle(Theme.ink)
                            Spacer()
                            Text(m0(r.wealth.last ?? 0) + " · " + outcome(r)).font(.mono(12)).foregroundStyle(Theme.muted)
                        }
                    }
                }
            } else {
                Computing()
            }
            Text(L("回放包含完整收支与人生大事；较近的起点受可用历史长度限制。")).font(.ui(11.5)).foregroundStyle(Theme.muted)
        }
    }

    private func outcome(_ r: ReplayOut) -> String {
        if let y = r.ruinedYear { return L("第 {n} 年出现资金不足", ["n": y]) }
        return r.complete ? L("撑过全程") : L("历史只到第 {n} 年，暂未出现资金不足", ["n": r.years.count - 1])
    }
}

func allocationText(_ w: [String: Double]) -> String {
    L("股 {s}% · 债 {b}% · 现金 {c}%", [
        "s": Int(((w["stocks"] ?? 0) * 100).rounded()), "b": Int(((w["bonds"] ?? 0) * 100).rounded()),
        "c": Int(((w["cash"] ?? 0) * 100).rounded()),
    ])
}

private struct AllocationCard: View {
    @Environment(AppModel.self) private var model
    let store: PlanStore
    let state: PlanState
    @State private var result: OptimizeOut?
    @State private var resultFor: PlanState?
    @State private var running = false
    @State private var error: String?

    var body: some View {
        let opt = resultFor == state ? result : nil
        let best = opt.flatMap { o in o.best.map { o.points[$0] } }
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(L("配置")).font(.display(20)).foregroundStyle(Theme.ink)
                Spacer()
                Button(running ? L("搜索中…") : L("寻找建议配置")) { Task { await run() } }
                    .buttonStyle(.keelOutlineCompact).disabled(running)
            }
            (Text(L("当前") + " ").foregroundColor(Theme.ink) + Text(allocationText(state.scenario.weights)).font(.mono(13)).foregroundColor(Theme.ink))
                .font(.ui(13))
            Text(L("比较 66 种配置，每档 10%，每种 2,000 条路径，约 8 秒。按长期增长搜索：资金不足路径占比 ≤ 5%，回撤 ≥ 40% 的路径占比 ≤ 50%。"))
                .font(.ui(11.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            if let opt {
                Hairline()
                if let best {
                    Text(L("建议") + " " + allocationText(best.weights)).font(.mono(14)).foregroundStyle(Theme.ink)
                    Grid(alignment: .trailing, horizontalSpacing: 24, verticalSpacing: 8) {
                        GridRow {
                            Text(L("结果")).gridColumnAlignment(.leading)
                            Text(L("当前"))
                            Text(L("建议"))
                        }
                        .font(.ui(11.5)).foregroundStyle(Theme.muted)
                        GridRow {
                            Text(L("资金覆盖路径占比")).gridColumnAlignment(.leading)
                            Text(pct1(opt.current.success)).font(.mono(12))
                            Text(pct1(best.success)).font(.mono(12))
                        }
                        GridRow {
                            Text(L("期末财富中位数")).gridColumnAlignment(.leading)
                            Text(m0(opt.current.median)).font(.mono(12))
                            Text(m0(best.median)).font(.mono(12))
                        }
                    }
                    .font(.ui(12.5)).foregroundStyle(Theme.ink)
                    Button(L("用这个配置")) {
                        store.update { $0.scenario.weights = best.weights }
                        model.banner = L("已把建议配置用到计划")
                    }
                    .buttonStyle(.keelOutlineCompact)
                } else {
                    Text(L("没有任何配置满足默认约束。可以调整收入、开销或退休时间后再试。")).font(.ui(13)).foregroundStyle(Theme.muted)
                }
            }
            if let error { ErrorLine(message: error) }
        }
        .padding(24)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Theme.border))
    }

    private func run() async {
        guard let api = model.api else { return }
        running = true
        defer { running = false }
        do {
            result = try await api.optimize(state)
            resultFor = state
            error = nil
        } catch {
            self.error = L("暂时算不出来，请检查输入或稍后重试。")
        }
    }
}

private struct LighthouseCard: View {
    let sim: SimOut?

    var body: some View {
        Card(title: L("三座灯塔")) {
            if let r = sim {
                let names = [L("收益 ≥ 储蓄"), L("收益 ≥ 开销"), L("财务独立")]
                DataTable(
                    columns: [L("目标"), L("目标资产"), L("到达概率"), L("中位到达")], widths: [130, 110, 150],
                    rows: (0..<3).map { i in
                        [
                            "\(i + 1) · " + names[i],
                            i < r.lighthouseTargets.count && r.lighthouseTargets[i] > 0 ? m0(r.lighthouseTargets[i]) : "—",
                            i < r.lighthouseProb.count ? pct1(r.lighthouseProb[i]) : "—",
                            (i < r.lighthouseMedianYear.count ? r.lighthouseMedianYear[i] : nil).map { L("第 {n} 年", ["n": String(format: "%.1f", $0)]) } ?? L("多数路径到不了"),
                        ]
                    }
                )
                Text(L("按配置的实际年收益 {p} 估算目标资产。", ["p": pct1(r.expectedReturn)])).font(.ui(11.5)).foregroundStyle(Theme.muted)
            } else {
                Computing()
            }
        }
    }
}

private struct SensitivityCard: View {
    @Environment(AppModel.self) private var model
    let state: PlanState
    @State private var result: SensitivityOut?
    @State private var resultFor: PlanState?
    @State private var running = false

    var body: some View {
        let sens = resultFor == state ? result : nil
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text(L("敏感度")).font(.display(20)).foregroundStyle(Theme.ink)
                Spacer()
                Button(running ? L("计算中…") : L("计算敏感度")) {
                    Task {
                        guard let api = model.api else { return }
                        running = true
                        defer { running = false }
                        result = try? await api.sensitivity(state)
                        resultFor = state
                    }
                }
                .buttonStyle(.keelOutlineCompact).disabled(running)
            }
            if let sens {
                Tornado(base: sens.baseSuccess, rows: sens.rows)
            } else {
                Text(L("把每个变量上下调整一档，看看哪项最影响计划的成功率。")).font(.ui(11.5)).foregroundStyle(Theme.muted)
            }
        }
        .padding(24)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Theme.border))
    }
}

/// Each bar spans the success rate between the low and high variant of one assumption.
private struct Tornado: View {
    let base: Double
    let rows: [SensitivityRow]

    var body: some View {
        let values = rows.flatMap { [$0.successLow, $0.successHigh] } + [base]
        let lo = (values.min() ?? 0) - 0.02
        let hi = (values.max() ?? 1) + 0.02
        VStack(alignment: .leading, spacing: 12) {
            ForEach(rows) { r in
                HStack(spacing: 12) {
                    Text(r.label).font(.ui(13)).foregroundStyle(Theme.ink).frame(width: 120, alignment: .leading)
                    GeometryReader { g in
                        let x = { (v: Double) in g.size.width * (v - lo) / (hi - lo) }
                        let a = min(r.successLow, r.successHigh)
                        let b = max(r.successLow, r.successHigh)
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 2).fill(Theme.series[1].opacity(0.7))
                                .frame(width: max(2, x(b) - x(a)), height: 12).offset(x: x(a))
                            Rectangle().fill(Theme.ink.opacity(0.6)).frame(width: 1, height: 20).offset(x: x(base))
                        }
                        .frame(maxHeight: .infinity)
                    }
                    .frame(height: 20)
                    Text("\(r.lowLabel) \(Format.percent(r.successLow)) · \(r.highLabel) \(Format.percent(r.successHigh))")
                        .font(.mono(11)).foregroundStyle(Theme.muted).frame(width: 170, alignment: .leading)
                }
                .help(L("中位期末财富：{a} {x} · {b} {y}", [
                    "a": r.lowLabel, "x": compactUSD(r.medianLow), "b": r.highLabel, "y": compactUSD(r.medianHigh),
                ]))
            }
            Text(L("竖线 = 当前情景成功率 {x} · 条越长，这个变量对结果越敏感 · 中位期末财富见悬停", ["x": Format.percent(base)]))
                .font(.ui(11.5)).foregroundStyle(Theme.muted)
        }
    }
}

/// A plain ledger table: the first column takes the room, the others are right-aligned numbers.
struct DataTable: View {
    let columns: [String]
    let widths: [CGFloat]  // for columns 1…n
    let rows: [[String]]
    var muted: Set<Int> = []

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                ForEach(Array(columns.enumerated()), id: \.offset) { i, c in
                    cell(c, i).font(.ui(10.5)).tracking(0.6).foregroundStyle(Theme.muted)
                }
            }
            .padding(.vertical, 8)
            Hairline(color: Theme.rule)
            ForEach(Array(rows.enumerated()), id: \.offset) { r, row in
                if r > 0 { Hairline(color: Theme.border.opacity(0.6)) }
                HStack(spacing: 0) {
                    ForEach(Array(row.enumerated()), id: \.offset) { i, v in
                        cell(v, i)
                            .font(i == 0 ? .ui(13) : .mono(12.5))
                            .foregroundStyle(muted.contains(i) ? Theme.muted : Theme.ink)
                    }
                }
                .padding(.vertical, 10)
            }
        }
    }

    @ViewBuilder private func cell(_ text: String, _ i: Int) -> some View {
        if i == 0 {
            Text(text).lineLimit(1).frame(maxWidth: .infinity, alignment: .leading)
        } else {
            Text(text).lineLimit(1).frame(width: i - 1 < widths.count ? widths[i - 1] : 100, alignment: .trailing)
        }
    }
}
