import Charts
import KeelCore
import SwiftUI

/// The heading of a 规划 tool: back to the workspace, the question, the answer in a sentence.
struct ToolHeading: View {
    @Environment(AppModel.self) private var model
    let question: String
    let answer: String
    var answerColor: Color = Theme.ink

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            LinkButton(title: "← " + L("规划"), arrow: false) { model.planTool = .workspace }
            Text(question).font(.ui(15)).foregroundStyle(Theme.muted)
            Text(answer).font(.display(36)).foregroundStyle(answerColor).fixedSize(horizontal: false, vertical: true)
        }
        .padding(.bottom, 16)
    }
}

/// 定投还是等跌了再买: every possible 10- and 20-year start in history, five ways to invest the
/// same monthly amount (including "smart DCA", which buys more in drawdowns).
struct BacktestView: View {
    @Environment(AppModel.self) private var model
    @State private var ticker = "QQQ"
    @State private var monthly = 1000.0
    @State private var prefilled = false
    @State private var dip = 10.0
    @State private var ma = 10.0
    @State private var more = false
    @State private var result: BacktestOut?
    @State private var submitted: Double?
    @State private var running = false
    @State private var error: String?

    static let strategies = ["dca", "lump", "dip", "ma", "smart"]

    var body: some View {
        Page {
            VStack(alignment: .leading, spacing: 20) {
                ToolHeading(question: L("定投还是等跌了再买？"), answer: answer)
                (Text(L("每月投入同样的钱，用不同规则投资，在")) + Text(L("每一个")).bold()
                    + Text(L("可能的 10 年、20 年起点上各跑一遍。价格含分红再投资，不计税费。")))
                    .font(.ui(13)).foregroundStyle(Theme.muted).frame(maxWidth: 760, alignment: .leading)
                HStack(alignment: .bottom, spacing: 16) {
                    Field(label: L("想买什么")) { TextInput(text: ticker) { ticker = $0.uppercased() } }
                    Field(label: L("每月投入")) { NumberField(value: monthly) { monthly = $0 } }.frame(width: 220)
                    Button(running ? L("回测中…") : L("回测")) { Task { await run(detail: nil) } }
                        .buttonStyle(SignalButtonStyle())
                        .disabled(running)
                }
                .id(prefilled)  // re-read the fields once the monthly amount is prefilled
                Text(prefilled ? L("每月投入先按你的收入减去开销预填，可以随时调整。") : L("目前没有每月结余，先用每月 $1,000 试算，可以随时调整。"))
                    .font(.ui(11.5)).foregroundStyle(Theme.muted)
                DisclosureRow(title: L("更多假设"), open: $more)
                if more {
                    HStack(spacing: 32) {
                        Field(label: L("跌多少才买"), hint: "\(Int(dip))%") { KeelSlider(value: dip, range: 5...50, step: 5) { dip = $0 } }
                        Field(label: L("均线月数"), hint: "\(Int(ma))") { KeelSlider(value: ma, range: 3...24, step: 1) { ma = $0 } }
                    }
                    .frame(maxWidth: 640)
                    HStack(spacing: 6) {
                        ForEach(["QQQ", "SPY", "VTI", "BND", "BTC-USD", "US-STOCKS-1871"], id: \.self) { sym in
                            Button(sym) { ticker = sym }.buttonStyle(.keelOutlineCompact)
                        }
                    }
                }
                if let error { ErrorLine(message: error) }
                if let o = result {
                    VStack(alignment: .leading, spacing: 44) {
                        Text("\(o.ticker) · " + L("数据") + " \(o.first) → \(o.last)").font(.mono(11.5)).foregroundStyle(Theme.muted)
                        ForEach(o.windows) { w in WindowBlock(window: w, labels: o.labels) { start in Task { await run(detail: start) } } }
                        if let d = o.detail { DetailBlock(detail: d, labels: o.labels) }
                    }
                    .opacity(running ? 0.6 : 1)
                    .padding(.top, 12)
                }
            }
        }
        .task {
            guard !prefilled, let api = model.api, let d = try? await api.simDefaults() else { return }
            let m = ((d.scenario.annualIncome - d.scenario.annualSpending) / 12).rounded()
            if m > 0 {
                monthly = m
                prefilled = true
            }
            await run(detail: nil)
        }
    }

    private var answer: String {
        guard let w = result?.windows.first else { return L("用历史上的每个起点，比较两种选择。") }
        return L("每月投入 {m}，定投在 {p} 的 {y} 年时段里胜过等跌再买。", [
            "m": m0(submitted ?? monthly), "p": pct1(w.dcaWinRate["dip"] ?? 0), "y": w.years,
        ])
    }

    private func run(detail: String?) async {
        guard let api = model.api else { return }
        running = true
        defer { running = false }
        do {
            result = try await api.backtest(ticker: ticker, monthly: monthly, dip: dip / 100, ma: Int(ma), detail: detail)
            submitted = monthly
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct SignalButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.ui(13, .medium))
            .foregroundStyle(Color(white: 0.08))
            .padding(.horizontal, 16)
            .padding(.vertical, 7)
            .background(Theme.signal.opacity(configuration.isPressed ? 0.8 : 1), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
    }
}

private struct WindowBlock: View {
    let window: BacktestWindow
    let labels: [String: String]
    let pick: (String) -> Void

    var body: some View {
        let w = window
        VStack(alignment: .leading, spacing: 12) {
            Text(L("{y} 年窗口 · {n} 个起点", ["y": w.years, "n": w.starts.count])).font(.display(21)).foregroundStyle(Theme.ink)
            (Text(L("每月定投在 ")) + Text(pct1(w.dcaWinRate["dip"] ?? 0)).font(.mono(13, .semibold))
                + Text(L(" 的窗口里赢过「等跌了再买」，在 ")) + Text(pct1(w.dcaWinRate["ma"] ?? 0)).font(.mono(13, .semibold))
                + Text(L(" 里赢过均线择时，在 ")) + Text(pct1(w.dcaWinRate["smart"] ?? 0)).font(.mono(13, .semibold))
                + Text(L(" 里赢过「回撤加码定投」。")))
                .font(.ui(13.5)).foregroundStyle(Theme.ink)
            Text(L("回撤加码定投：钱一样多，离最高点 5% 以内只投一半，跌 5–15% 投 1 份，15–30% 投 2 份，更深投 3 份，没投的钱先放现金。"))
                .font(.ui(11.5)).foregroundStyle(Theme.muted)
            DataTable(
                columns: [L("策略"), L("期末/投入 中位"), L("最差 5%"), L("IRR 中位"), L("最大回撤 中位"), L("最大回撤 最差"), L("定投胜率"), L("最差起点")],
                widths: [120, 90, 90, 120, 100, 100, 110],
                rows: BacktestView.strategies.map { k in
                    let s = w.summary[k]
                    return [
                        labels[k] ?? k,
                        s.map { String(format: "%.2f×", $0.multipleP50) } ?? "—",
                        s.map { String(format: "%.2f×", $0.multipleP5) } ?? "—",
                        s.map { pct1($0.irrP50) } ?? "—",
                        s.map { pct1($0.mddP50) } ?? "—",
                        (s?.mddMax).map { pct1($0) } ?? "—",
                        k == "dca" ? "—" : pct1(w.dcaWinRate[k] ?? 0),
                        w.worstStart[k] ?? "—",
                    ]
                },
                muted: [2, 4, 7]
            )
            Text(L("一次性投入假设第一天就有全部的钱，期末倍数和定投不完全可比；IRR 更公平。横轴 = 起始月，纵轴 = 期末/投入。"))
                .font(.ui(11.5)).foregroundStyle(Theme.muted)
            LineExhibitChart(
                xLabels: w.starts,
                series: BacktestView.strategies.enumerated().map { i, k in
                    ExhibitSeries(label: labels[k] ?? k, values: w.multiple[k] ?? [], color: Theme.series[i], width: 1.8, strong: i == 0)
                },
                format: { String(format: "%.1f×", $0) },
                axis: { v, _ in String(format: "%.0f×", v) },
                height: 260,
                rightMargin: 180,
                onPick: { j in if j < w.starts.count { pick(w.starts[j]) } }
            )
        }
    }
}

private struct DetailBlock: View {
    let detail: BacktestDetail
    let labels: [String: String]

    var body: some View {
        let d = detail
        VStack(alignment: .leading, spacing: 8) {
            Text(L("从 {d} 开始的 10 年", ["d": d.start])).font(.display(21)).foregroundStyle(Theme.ink)
            Text(L("在上面的图里点任意起点切换 · 虚线 = 累计投入")).font(.ui(11.5)).foregroundStyle(Theme.muted)
            LineExhibitChart(
                xLabels: d.months,
                series: BacktestView.strategies.enumerated().map { i, k in
                    ExhibitSeries(label: labels[k] ?? k, values: d.value[k] ?? [], color: Theme.series[i], strong: i == 0)
                } + [ExhibitSeries(label: L("累计投入"), values: d.contributed, color: Theme.muted, width: 1.5, dash: [4, 3], strong: false)],
                height: 260,
                rightMargin: 180
            )
            HStack(spacing: 20) {
                ForEach(BacktestView.strategies, id: \.self) { k in
                    Text("\(labels[k] ?? k) IRR \(pct1(d.irr[k] ?? 0)) · " + L("回撤") + " \(pct1(d.mdd[k] ?? 0))")
                        .font(.mono(11)).foregroundStyle(Theme.muted)
                }
            }
        }
    }
}
