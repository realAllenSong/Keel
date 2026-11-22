import Charts
import KeelCore
import SwiftUI

/// Pieces shared by 本月, 月报 and 明细: alert rows, the axis styling of Swift Charts, the
/// net-worth chart of 资产, the to-do list and the first-run screen.
struct AlertRow: View {
    let alert: PulseAlert

    var body: some View {
        let color = alert.severity == "warning" ? Theme.outflow : Theme.signal
        HStack(alignment: .top, spacing: 12) {
            Dot(color: color).padding(.top, 5)
            VStack(alignment: .leading, spacing: 2) {
                Text(alert.title).font(.ui(13, .medium)).foregroundStyle(Theme.ink)
                Text(alert.body).font(.ui(13)).foregroundStyle(Theme.muted)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(color.opacity(0.1), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

// MARK: - charts (shared axis styling)

extension View {
    /// Mono labels in the chart's muted ink, hairline grid, an axis line along the bottom.
    func keelAxes(x xValues: [Int]? = nil, yFormat: @escaping (Double) -> String = compactUSD) -> some View {
        self
            .chartYAxis {
                AxisMarks(position: .leading, values: .automatic(desiredCount: 5)) { v in
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 1)).foregroundStyle(Theme.chartGrid)
                    AxisValueLabel {
                        if let d = v.as(Double.self) { Text(yFormat(d)).font(.mono(10)).foregroundStyle(Theme.chartMuted) }
                    }
                }
            }
            .chartPlotStyle { plot in
                plot.overlay(alignment: .bottom) { Rectangle().fill(Theme.chartAxis).frame(height: 1) }
            }
    }
}

/// Cumulative spending: the usual range (band), the usual path (dashed), this month (solid) and
/// the forecast to month end (dotted), with the budget as a horizontal rule.
struct NetworthChart: View {
    let series: [NWPoint]

    static var classes: [(key: String, label: String, slot: Int)] {
        [
        ("cash", L("现金"), 0), ("brokerage", L("券商"), 1), ("retirement", L("退休账户"), 2), ("crypto", L("加密"), 3),
        ("term_deposit", L("定期存款"), 7),
        ("realEstate", L("房产"), 4), ("vehicle", L("车辆"), 5), ("other", L("其他"), 6),
        ]
    }

    private var present: [(key: String, label: String, slot: Int)] {
        Self.classes.filter { c in series.contains { value($0, c.key) != 0 } }
    }

    private func value(_ p: NWPoint, _ key: String) -> Double {
        (p.byClass[key] ?? (key == "realEstate" ? p.byClass["real_estate"] : nil))?.double ?? 0
    }

    var body: some View {
        let classes = present
        let step = max(1, series.count / 7)
        let ticks = stride(from: step / 2 + 1, to: series.count - 1, by: step).map { series[$0].month }
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 14) {
                ForEach(classes, id: \.key) { LegendItem(color: Theme.series[$0.slot], label: $0.label) }
                LegendItem(color: Theme.ink, label: L("净资产"))
            }
            Chart {
                ForEach(classes, id: \.key) { c in
                    ForEach(series) { p in
                        AreaMark(x: .value("month", p.month), y: .value("usd", value(p, c.key)), stacking: .standard)
                            .foregroundStyle(by: .value("class", c.key))
                            .interpolationMethod(.linear)
                    }
                }
                ForEach(series) { p in
                    LineMark(x: .value("month", p.month), y: .value("usd", p.netWorth.double), series: .value("s", "nw"))
                        .foregroundStyle(Theme.ink)
                        .lineStyle(StrokeStyle(lineWidth: 2))
                }
            }
            .chartForegroundStyleScale(
                domain: classes.map(\.key), range: classes.map { Theme.series[$0.slot].opacity(0.85) }
            )
            .chartLegend(.hidden)
            .chartXAxis {
                AxisMarks(values: ticks) { v in
                    AxisValueLabel {
                        if let s = v.as(String.self) { Text(s).font(.mono(10)).foregroundStyle(Theme.chartMuted) }
                    }
                }
            }
            .keelAxes()
        }
    }
}

/// One track to financial independence: you (⛵) and three lighthouses.
struct TodosCard: View {
    @Environment(AppModel.self) private var model
    let todos: [Todo]
    let more: Int

    var body: some View {
        Card(title: L("需要你做的事")) {
            if todos.isEmpty {
                (Text("✓  ").foregroundColor(Theme.signalInk) + Text(L("都处理好了，这个月没有需要你做的事。")).foregroundColor(Theme.muted))
                    .font(.ui(13))
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(todos.enumerated()), id: \.offset) { i, t in
                        if i > 0 { Hairline() }
                        HStack(spacing: 12) {
                            Dot(color: i == 0 ? Theme.signal : Theme.ink.opacity(0.25), size: 9)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(t.title).font(.ui(13, .medium)).foregroundStyle(Theme.ink)
                                if let d = t.detail {
                                    Text(d).font(.ui(11.5)).foregroundStyle(Theme.muted).lineLimit(2)
                                }
                            }
                            Spacer(minLength: 8)
                            Button(t.action + (t.external == nil ? " →" : " ↗")) { open(t) }
                                .buttonStyle(.keelPrimaryCompact)
                        }
                        .padding(.vertical, 12)
                    }
                }
            }
            if more > 0 {
                Text(L("还有 {n} 件小事，处理完上面的会自动出现。", ["n": more])).font(.ui(12)).foregroundStyle(Theme.muted)
            }
        }
    }

    private func open(_ t: Todo) {
        if let external = t.external, let url = URL(string: external) {
            NSWorkspace.shared.open(url)
        } else if let href = t.href {
            model.route(href)
        }
    }
}

// MARK: - first run

struct FirstRun: View {
    @Environment(AppModel.self) private var model
    let home: Home

    var body: some View {
        VStack(alignment: .leading, spacing: 36) {
            VStack(alignment: .leading, spacing: 12) {
                Text(L("起航之前，先看清自己在哪")).font(.display(38)).foregroundStyle(Theme.ink)
                Text(L("工资是风，资产带来的收入是洋流。Keel 帮你把每一笔账记清楚，再告诉你离下一座灯塔还有多远。"))
                    .font(.ui(17)).foregroundStyle(Theme.muted).lineSpacing(3)
            }
            Button { model.chooseFiles() } label: {
                VStack(spacing: 8) {
                    Text(L("把第一份账单拖进来")).font(.display(24)).foregroundStyle(Theme.ink)
                    Text(L("或者点这里选择。银行的 CSV / Excel、支付宝和微信的压缩包都可以；放在「下载」文件夹里的也会被自动找到。"))
                        .font(.ui(13)).foregroundStyle(Theme.muted).multilineTextAlignment(.center)
                }
                .padding(.vertical, 56)
                .padding(.horizontal, 24)
                .frame(maxWidth: .infinity)
                .background(Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .strokeBorder(Theme.rule, style: StrokeStyle(lineWidth: 2, dash: [7, 5]))
                )
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            Button { model.showGuide = true } label: {
                (Text(L("还没有账单？")).fontWeight(.medium).foregroundColor(Theme.ink)
                    + Text("  " + L("看看 Chase、Amex、支付宝、微信分别怎么下载 →")).foregroundColor(Theme.muted))
                    .font(.ui(13))
                    .padding(.horizontal, 20).padding(.vertical, 16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.card, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Theme.border))
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .padding(.top, -20)
            VStack(alignment: .leading, spacing: 12) {
                ForEach(Array(home.firstRun.enumerated()), id: \.element.id) { i, step in
                    HStack(spacing: 12) {
                        ZStack {
                            Circle().fill(step.done ? Theme.signal : .clear)
                            Circle().strokeBorder(step.done ? Theme.signal : Theme.rule)
                            Text(step.done ? "✓" : "\(i + 1)").font(.ui(12)).foregroundStyle(step.done ? Color(white: 0.1) : Theme.muted)
                        }
                        .frame(width: 26, height: 26)
                        Text(step.label).font(.ui(14)).foregroundStyle(step.done ? Theme.muted : Theme.ink)
                            .strikethrough(step.done)
                    }
                }
            }
            if !home.todos.isEmpty { TodosCard(todos: home.todos, more: home.moreTodos) }
        }
        .frame(maxWidth: 672)
        .frame(maxWidth: .infinity)
        .padding(.top, 24)
    }
}
