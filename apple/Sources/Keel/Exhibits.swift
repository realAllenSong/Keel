import KeelCore
import SwiftUI

/// Charts drawn as research exhibits: a numbered title that states the finding, units and basis in
/// the subtitle, values written on the data instead of a legend, the value axis on the right,
/// hairline grids and a source line. Drawn with Canvas so every label sits exactly where it reads
/// best (Swift Charts places annotations for you, which is what these charts avoid).
struct Exhibit<Content: View, Key: View>: View {
    let number: Int
    let title: String
    let sub: String
    var note: String?
    var asOf: String?
    @ViewBuilder var content: Content
    @ViewBuilder var key: Key

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .bottom, spacing: 24) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(L("图 {n}", ["n": number])).font(.mono(11, .medium)).foregroundStyle(Theme.signal)
                    Text(title).font(.display(16, .semibold)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(sub).font(.ui(11.5)).foregroundStyle(Theme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
                key
            }
            content
            VStack(spacing: 6) {
                Hairline()
                Text(source).font(.ui(10.5)).foregroundStyle(Theme.muted)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var source: String {
        let src = asOf.map { L("资料来源：Keel 账本，截至 {d}。", ["d": Format.day($0)]) } ?? L("资料来源：Keel 账本。")
        return (note ?? "") + src
    }
}

extension Exhibit where Key == EmptyView {
    init(number: Int, title: String, sub: String, note: String? = nil, asOf: String? = nil, @ViewBuilder content: () -> Content) {
        self.init(number: number, title: title, sub: sub, note: note, asOf: asOf, content: content, key: { EmptyView() })
    }
}

/// The key above a timeline: line, dashed line, filled and hollow squares.
struct TimelineKey: View {
    var projected = true

    var body: some View {
        HStack(spacing: 16) {
            item(L("余额")) { Rectangle().fill(Theme.line).frame(width: 16, height: 2.5) }
            if projected {
                item(L("预计余额")) {
                    Path { p in p.move(to: .zero); p.addLine(to: CGPoint(x: 16, y: 0)) }
                        .stroke(Theme.line, style: StrokeStyle(lineWidth: 2, dash: [4, 3])).frame(width: 16, height: 2)
                }
            }
            item(L("收入")) { Rectangle().fill(Theme.line).frame(width: 8, height: 8) }
            item(L("花销")) { Rectangle().fill(Theme.line2).frame(width: 8, height: 8) }
            if projected {
                item(L("计划中的账单和收入")) { Rectangle().strokeBorder(Theme.muted, lineWidth: 1.25).frame(width: 8, height: 8) }
            }
        }
    }

    private func item<S: View>(_ label: String, @ViewBuilder swatch: () -> S) -> some View {
        HStack(spacing: 6) {
            swatch()
            Text(label).font(.ui(11)).foregroundStyle(Theme.muted)
        }
    }
}

/// Four or five figures in a ruled row: label, mono figure, one line of context.
struct Figs: View {
    struct Item: Identifiable {
        let label: String
        let value: String
        var sub: String?
        var accent = false
        var action: (() -> Void)?
        var id: String { label }
    }

    let items: [Item]

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            ForEach(Array(items.enumerated()), id: \.element.id) { i, item in
                if i > 0 { VHairline(color: Theme.border) }
                Group {
                    if let action = item.action {
                        Button(action: action) { figure(item) }.buttonStyle(.plain)
                            .help(L("查看每笔明细"))
                    } else { figure(item) }
                }
                .padding(.leading, i == 0 ? 0 : 16)
                .padding(.trailing, 12)
                .padding(.vertical, 12)
                .frame(maxWidth: .infinity, alignment: .topLeading)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
        .overlay(alignment: .top) { Rectangle().fill(Theme.ink).frame(height: 1) }
        .overlay(alignment: .bottom) { Rectangle().fill(Theme.ink).frame(height: 1) }
    }

    private func figure(_ item: Item) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(item.label).font(.ui(12)).foregroundStyle(Theme.muted)
            Text(item.value).font(.serif(26)).foregroundStyle(Theme.ink)
                .lineLimit(1).minimumScaleFactor(0.7)
                .overlay(alignment: .bottomLeading) {
                    if item.accent { Rectangle().fill(Theme.signal).frame(width: 28, height: 2).offset(y: 5) }
                }
            if let sub = item.sub {
                Text(sub).font(.ui(11)).foregroundStyle(Theme.muted).lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

// MARK: - drawing helpers

/// Thin wrapper over GraphicsContext with the strokes and labels the exhibits use.
struct Pen {
    let ctx: GraphicsContext

    func line(_ a: CGPoint, _ b: CGPoint, _ color: Color, width: CGFloat = 1, dash: [CGFloat] = []) {
        var p = Path()
        p.move(to: a)
        p.addLine(to: b)
        ctx.stroke(p, with: .color(color), style: StrokeStyle(lineWidth: width, lineCap: dash.isEmpty ? .butt : .round, dash: dash))
    }

    func poly(_ pts: [CGPoint], _ color: Color, width: CGFloat, dash: [CGFloat] = []) {
        guard pts.count > 1 else { return }
        var p = Path()
        p.addLines(pts)
        ctx.stroke(p, with: .color(color), style: StrokeStyle(lineWidth: width, lineCap: .round, lineJoin: .round, dash: dash))
    }

    func area(_ pts: [CGPoint], _ color: Color) {
        guard pts.count > 2 else { return }
        var p = Path()
        p.addLines(pts)
        p.closeSubpath()
        ctx.fill(p, with: .color(color))
    }

    func rect(_ r: CGRect, _ color: Color) { ctx.fill(Path(r), with: .color(color)) }

    func box(_ r: CGRect, _ color: Color, width: CGFloat = 1.25) {
        ctx.stroke(Path(r.insetBy(dx: width / 2, dy: width / 2)), with: .color(color), lineWidth: width)
    }

    func dot(_ c: CGPoint, _ r: CGFloat, _ color: Color) {
        ctx.fill(Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r)), with: .color(color))
    }

    func ring(_ c: CGPoint, _ r: CGFloat, _ color: Color) {
        let e = Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r))
        ctx.fill(e, with: .color(Theme.card))
        ctx.stroke(e, with: .color(color), lineWidth: 1.5)
    }

    /// `anchor` as in Canvas: .leading puts the text's left edge (vertically centred) on `p`.
    func text(
        _ s: String, _ p: CGPoint, _ anchor: UnitPoint = .leading, size: CGFloat = 11.5,
        weight: Font.Weight = .regular, color: Color = Theme.muted, mono: Bool = false
    ) {
        let t = Text(s).font(mono ? .mono(size, weight) : .ui(size, weight)).foregroundColor(color)
        ctx.draw(t, at: p, anchor: anchor)
    }

    func axis(_ s: String, _ p: CGPoint, _ anchor: UnitPoint = .leading) {
        text(s, p, anchor, size: 10.5, color: Theme.muted, mono: true)
    }
}

extension Pen {
    /// A small card of lines next to `x` (flipped to the left near the right edge).
    func tooltip(_ lines: [(String, Bool)], x: CGFloat, top: CGFloat, width: CGFloat) {
        guard !lines.isEmpty else { return }
        let resolved = lines.map { line in
            ctx.resolve(Text(line.0).font(.ui(11.5, line.1 ? .semibold : .regular)).foregroundColor(line.1 ? Theme.ink : Theme.muted))
        }
        let sizes = resolved.map { $0.measure(in: CGSize(width: 320, height: 40)) }
        let boxW = (sizes.map(\.width).max() ?? 0) + 18
        let boxH = sizes.reduce(0) { $0 + $1.height + 3 } + 12
        let bx = x + 12 + boxW > width ? x - 12 - boxW : x + 12
        let box = CGRect(x: bx, y: top, width: boxW, height: boxH)
        let shape = Path(roundedRect: box, cornerRadius: 6)
        ctx.fill(shape, with: .color(Theme.card))
        ctx.stroke(shape, with: .color(Theme.rule), lineWidth: 1)
        var y = top + 7
        for (r, size) in zip(resolved, sizes) {
            ctx.draw(r, at: CGPoint(x: bx + 9, y: y), anchor: .topLeading)
            y += size.height + 3
        }
    }

    func guide(_ x: CGFloat, from top: CGFloat, to bottom: CGFloat) {
        line(CGPoint(x: x, y: top), CGPoint(x: x, y: bottom), Theme.ink.opacity(0.35))
    }
}

/// A Canvas exhibit you can point at and click: `draw` paints it, `readout` paints the hover
/// layer for the pointer, `pick` handles a click (both optional).
struct InteractiveCanvas: View {
    let height: CGFloat
    let label: String
    let draw: (Pen, CGSize) -> Void
    var readout: ((Pen, CGSize, CGPoint) -> Void)?
    var pick: ((CGSize, CGPoint) -> Void)?
    @State private var hover: CGPoint?

    var body: some View {
        GeometryReader { geo in
            Canvas { ctx, size in
                let pen = Pen(ctx: ctx)
                draw(pen, size)
                if let hover, let readout { readout(pen, size, hover) }
            }
            .contentShape(Rectangle())
            .onContinuousHover { phase in
                switch phase {
                case let .active(p): hover = p
                case .ended: hover = nil
                }
            }
            .onTapGesture(coordinateSpace: .local) { p in pick?(geo.size, p) }
        }
        .frame(height: height)
        .accessibilityElement()
        .accessibilityLabel(label)
    }
}

/// A round tick step for `span` split into about `ticks` parts (1, 2, 2.5 or 5 × 10ⁿ).
func niceStep(_ span: Double, ticks: Int = 5) -> Double {
    guard span > 0 else { return 1 }
    let raw = span / Double(max(1, ticks))
    let mag = pow(10, floor(log10(raw)))
    for m in [1.0, 2, 2.5, 5, 10] where m * mag >= raw { return m * mag }
    return 10 * mag
}

/// "$4.5k" / "$62.5k" / "$1.2M": keeps one decimal when the step needs it.
func axisUSD(_ v: Double, step: Double, currency: String = "USD") -> String {
    let symbol = currency == "USD" ? "$" : currency == "CNY" ? "¥" : currency + " "
    if v == 0 { return symbol + "0" }
    let a = abs(v)
    let sign = v < 0 ? "−" : ""
    if a >= 1_000_000 { return sign + symbol + String(format: "%.1fM", a / 1_000_000) }
    if a >= 1000 || step >= 1000 {
        let whole = step.truncatingRemainder(dividingBy: 1000) == 0
        return sign + symbol + String(format: whole ? "%.0fk" : "%.1fk", a / 1000)
    }
    return sign + symbol + String(format: "%.0f", a)
}

func signedPct(_ v: Double) -> String { (v >= 0 ? "+" : "−") + String(format: "%.0f%%", abs(v) * 100) }

/// Calendar arithmetic on the engine's yyyy-MM-dd strings (UTC, no time of day).
enum Day {
    static let cal: Calendar = {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        return c
    }()

    static func date(_ s: String) -> Date? {
        let p = s.split(separator: "-").compactMap { Int($0) }
        guard p.count == 3 else { return nil }
        return cal.date(from: DateComponents(year: p[0], month: p[1], day: p[2]))
    }

    static func string(_ d: Date) -> String {
        let c = cal.dateComponents([.year, .month, .day], from: d)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }

    static func between(_ a: String, _ b: String) -> Int {
        guard let x = date(a), let y = date(b) else { return 0 }
        return cal.dateComponents([.day], from: x, to: y).day ?? 0
    }

    static func add(_ s: String, _ days: Int) -> String {
        guard let d = date(s), let r = cal.date(byAdding: .day, value: days, to: d) else { return s }
        return string(r)
    }

    /// "2026-09" -> ("2026-09-01", "2026-09-30")
    static func monthBounds(_ ym: String) -> (String, String) {
        let first = ym + "-01"
        let next = Day.shiftMonth(ym, 1) + "-01"
        return (first, add(next, -1))
    }

    static func dayOfMonth(_ s: String) -> Int { Int(s.suffix(2)) ?? 0 }

    /// "2026-09" + 2 -> "2026-11"
    static func shiftMonth(_ ym: String, _ delta: Int) -> String {
        let p = ym.split(separator: "-").compactMap { Int($0) }
        guard p.count == 2 else { return ym }
        let total = p[0] * 12 + (p[1] - 1) + delta
        return String(format: "%04d-%02d", total / 12, total % 12 + 1)
    }
}

// MARK: - 1. the month so far

/// Cumulative spending this month against the usual band and path, the forecast to month end,
/// a callout at today and the two end values in the right margin.
struct PaceExhibitChart: View {
    let pulse: Pulse
    /// A day of this month was clicked (yyyy-MM-dd): show its transactions.
    var onPickDay: ((String) -> Void)?

    private var currency: String { pulse.currency ?? "USD" }
    private func amount(_ value: Money) -> String { m0(value, currency: currency) }
    private func amount(_ value: Double) -> String { m0(value, currency: currency) }

    var body: some View {
        InteractiveCanvas(height: 300, label: L("本月累计花销和平时对比"), draw: draw, readout: readout, pick: pick)
    }

    private static let margins: (l: CGFloat, r: CGFloat, t: CGFloat, b: CGFloat) = (8, 170, 18, 28)

    private func day(at p: CGPoint, _ size: CGSize) -> Int? {
        guard let last = pulse.curve.last?.day, last > 1 else { return nil }
        let m = Self.margins
        let f = (p.x - m.l) / (size.width - m.l - m.r)
        guard f >= -0.02, f <= 1.02 else { return nil }
        return min(last, max(1, Int((f * CGFloat(last - 1)).rounded()) + 1))
    }

    private func iso(_ d: Int) -> String { (pulse.month ?? String((pulse.asOf ?? "").prefix(7))) + String(format: "-%02d", d) }

    private func readout(_ pen: Pen, _ size: CGSize, _ p: CGPoint) {
        guard let d = day(at: p, size), let last = pulse.curve.last?.day else { return }
        let m = Self.margins
        let x = m.l + CGFloat(d - 1) / CGFloat(last - 1) * (size.width - m.l - m.r)
        pen.guide(x, from: m.t, to: size.height - m.b)
        let c = pulse.curve[d - 1]
        var lines: [(String, Bool)] = [(Format.day(iso(d)), true)]
        if let a = c.actual { lines.append((L("本月累计 {x}", ["x": amount(a)]), true)) }
        lines.append((L("平时 {x}", ["x": amount(c.typical)]), false))
        lines.append((L("常见范围 {a}–{b}", ["a": amount(c.low), "b": amount(c.high)]), false))
        if c.actual != nil, onPickDay != nil { lines.append((L("点一下看当天的交易"), false)) }
        pen.tooltip(lines, x: x, top: m.t + 4, width: size.width)
    }

    private func pick(_ size: CGSize, _ p: CGPoint) {
        guard let d = day(at: p, size), pulse.curve[d - 1].actual != nil else { return }
        onPickDay?(iso(d))
    }

    private func draw(_ pen: Pen, _ size: CGSize) {
        let curve = pulse.curve
        guard let last = curve.last?.day, last > 1 else { return }
        let today = curve.last(where: { $0.actual != nil })?.day ?? 0
        let (ml, mr, mt, mb) = Self.margins
        let w = size.width, h = size.height
        let spent = pulse.spent.double, forecast = pulse.forecast.double
        let highs = curve.map(\.high.double) + [forecast, curve.last?.typical.double ?? 0, spent]
        let lows = curve.map(\.low.double) + curve.compactMap { $0.actual?.double }
        let step = niceStep((highs.max() ?? 1) - (lows.min() ?? 0), ticks: 6)
        let ymax = ceil((highs.max() ?? 1) / step) * step
        let ymin = max(0, floor((lows.min() ?? 0) / step) * step)
        guard ymax > ymin else { return }
        let X = { (d: Int) in ml + CGFloat(d - 1) / CGFloat(last - 1) * (w - ml - mr) }
        let Y = { (v: Double) in mt + CGFloat(1 - (v - ymin) / (ymax - ymin)) * (h - mt - mb) }

        var v = ymin
        while v <= ymax + 0.5 {
            pen.line(CGPoint(x: ml, y: Y(v)), CGPoint(x: w - mr, y: Y(v)), v == ymin ? Theme.chartAxis : Theme.chartGrid)
            pen.axis(axisUSD(v, step: step, currency: currency), CGPoint(x: w - mr + 8, y: Y(v)))
            v += step
        }
        let upper: [CGPoint] = curve.map { CGPoint(x: X($0.day), y: Y($0.high.double)) }
        let lower: [CGPoint] = curve.reversed().map { CGPoint(x: X($0.day), y: Y($0.low.double)) }
        pen.area(upper + lower, Theme.band)
        pen.poly(curve.map { CGPoint(x: X($0.day), y: Y($0.typical.double)) }, Theme.muted, width: 1.25)
        let actual: [CGPoint] = curve.compactMap { d -> CGPoint? in
            guard let a = d.actual else { return nil }
            return CGPoint(x: X(d.day), y: Y(a.double))
        }

        if today > 0 {
            let tx = X(today), ty = Y(spent)
            pen.line(CGPoint(x: tx, y: mt), CGPoint(x: tx, y: h - mb), Theme.rule, dash: [3, 3])
            pen.text(L("今天 {d}", ["d": Format.day(pulse.asOf ?? "")]), CGPoint(x: tx + 6, y: mt + 6), size: 11)
            if today < last {
                pen.line(CGPoint(x: tx, y: ty), CGPoint(x: X(last), y: Y(forecast)), Theme.line, width: 1.5, dash: [2, 4])
            }
            pen.poly(actual, Theme.line, width: 2.5)
            pen.dot(CGPoint(x: tx, y: ty), 4, Theme.signal)
            let typ = curve[min(today, curve.count) - 1].typical.double
            let diff = typ > 0 ? (spent - typ) / typ : 0
            let cy = max(mt + 24, ty - 30)
            pen.text(L("已花 {x}", ["x": amount(spent)]), CGPoint(x: tx - 8, y: cy), .trailing, size: 12, weight: .semibold, color: Theme.ink)
            pen.text(L("平时同期 {x}（{p}）", ["x": amount(typ), "p": signedPct(diff)]), CGPoint(x: tx - 8, y: cy + 16), .trailing, size: 11.5)
        } else {
            pen.poly(actual, Theme.line, width: 2.5)
        }

        // End values, nudged apart so they never overlap.
        var ends: [(CGFloat, String, Bool)] = [(Y(curve.last?.typical.double ?? 0), L("平时 {x}", ["x": amount(curve.last?.typical ?? Money("0"))]), false)]
        if today < last { ends.append((Y(forecast), L("预计 {x}", ["x": amount(forecast)]), true)) }
        ends.sort { $0.0 < $1.0 }
        if ends.count == 2, ends[1].0 - ends[0].0 < 16 { ends[1].0 = ends[0].0 + 16 }
        for (y, s, strong) in ends {
            pen.text(s, CGPoint(x: w - mr + 56, y: y), size: 12, weight: strong ? .semibold : .regular, color: strong ? Theme.ink : Theme.muted)
        }

        if pulse.historyMonths >= 2 {
            let mid = curve[curve.count * 3 / 4]
            let label = pulse.historyMonths >= 4
                ? L("过去 {n} 个月的中间一半", ["n": pulse.historyMonths])
                : L("过去 {n} 个月的范围", ["n": pulse.historyMonths])
            pen.text(label, CGPoint(x: X(mid.day), y: min(h - mb - 10, Y(mid.low.double) + 14)), .center, size: 11)
        }
        for d in [1, 8, 15, 22, 29] where d <= last {
            pen.axis("\(d)", CGPoint(x: X(d), y: h - 10), .center)
        }
        if last - 29 >= 2 { pen.axis("\(last)", CGPoint(x: X(last), y: h - 10), .center) }
    }
}

// MARK: - the cash timeline

/// One axis through time: the cash accounts' balance (solid up to today, dashed after, from the
/// engine's projection), over each day's money in (up) and out (down). Planned bills and paydays
/// are hollow. Bars are square-root scaled so rent does not flatten every other day.
struct CashTimelineChart: View {
    let start: String
    let end: String
    let days: [CashFlowDay]
    var projection: [CashDay] = []
    var plans: [PulseEvent] = []
    var today: String?
    /// A past day was clicked (yyyy-MM-dd): show its transactions.
    var onPickDay: ((String) -> Void)?

    var currency = "USD"
    private func amount(_ value: Money) -> String { m0(value, currency: currency) }
    private func amount(_ value: Double) -> String { m0(value, currency: currency) }

    var body: some View {
        InteractiveCanvas(height: 380, label: L("现金账户余额和每天的进出"), draw: draw, readout: readout, pick: pick)
    }

    private static let margins: (l: CGFloat, r: CGFloat, t: CGFloat) = (8, 110, 16)

    private func date(at p: CGPoint, _ size: CGSize) -> String? {
        let span = max(1, Day.between(start, end))
        let m = Self.margins
        let f = (p.x - m.l) / (size.width - m.l - m.r)
        guard f >= -0.02, f <= 1.02 else { return nil }
        return Day.add(start, min(span, max(0, Int((f * CGFloat(span)).rounded()))))
    }

    private func readout(_ pen: Pen, _ size: CGSize, _ p: CGPoint) {
        guard let d = date(at: p, size) else { return }
        let span = max(1, Day.between(start, end))
        let m = Self.margins
        let x = m.l + CGFloat(Day.between(start, d)) / CGFloat(span) * (size.width - m.l - m.r)
        pen.guide(x, from: m.t, to: size.height - 20)
        var lines: [(String, Bool)] = [(Format.day(d), true)]
        if let day = days.first(where: { $0.date == d }) {
            lines.append((L("余额 {x}", ["x": amount(day.balance)]), true))
            if day.inflow.double > 0 { lines.append((L("进 {x}", ["x": amount(day.inflow)]), false)) }
            if day.outflow.double > 0 { lines.append((L("出 {x}", ["x": amount(day.outflow)]), false)) }
            if onPickDay != nil, day.inflow.double > 0 || day.outflow.double > 0 { lines.append((L("点一下看当天的交易"), false)) }
        } else if let proj = projection.first(where: { $0.date == d }) {
            lines.append((L("预计余额 {x}", ["x": amount(proj.balance)]), true))
        }
        for e in plans where e.date == d && !e.overdue {
            lines.append((eventName(e) + " " + Format.money(e.amount.value, digits: 0, sign: !e.amount.isNegative), false))
        }
        pen.tooltip(lines, x: x, top: m.t + 4, width: size.width)
    }

    private func pick(_ size: CGSize, _ p: CGPoint) {
        guard let d = date(at: p, size), days.contains(where: { $0.date == d }) else { return }
        onPickDay?(d)
    }

    private func draw(_ pen: Pen, _ size: CGSize) {
        let span = max(1, Day.between(start, end))
        let (ml, mr, mt) = Self.margins
        let balH: CGFloat = 170, gap: CGFloat = 34
        let w = size.width, h = size.height
        let flowH = h - mt - balH - gap - 40
        let X = { (d: String) in ml + CGFloat(Day.between(start, d)) / CGFloat(span) * (w - ml - mr) }
        let past = days.filter { $0.date >= start && $0.date <= end }
        let future = projection.filter { $0.date >= start && $0.date <= end }
        let values = past.map(\.balance.double) + future.map(\.balance.double)
        guard let lo = values.min(), let hi = values.max() else { return }
        let step = niceStep(max(hi - lo, 1000), ticks: 4)
        let ymin = floor(lo / step) * step, ymax = max(ceil(hi / step) * step, ymin + step)
        let Y = { (v: Double) in mt + CGFloat(1 - (v - ymin) / (ymax - ymin)) * balH }

        var v = ymin
        while v <= ymax + 0.5 {
            pen.line(CGPoint(x: ml, y: Y(v)), CGPoint(x: w - mr, y: Y(v)), Theme.chartGrid)
            pen.axis(axisUSD(v, step: step, currency: currency), CGPoint(x: w - mr + 8, y: Y(v)))
            v += step
        }
        pen.text(L("现金账户余额"), CGPoint(x: ml + 4, y: mt + 10), size: 11)
        // Month rules after the first month.
        var m = Day.shiftMonth(String(start.prefix(7)), 1)
        while m + "-01" <= end {
            let x = X(m + "-01")
            pen.line(CGPoint(x: x, y: mt), CGPoint(x: x, y: h - 20), Theme.rule)
            pen.text(Format.month(m, short: true), CGPoint(x: x + 6, y: mt + 10), size: 11.5, weight: .semibold, color: Theme.ink)
            m = Day.shiftMonth(m, 1)
        }

        pen.poly(past.map { CGPoint(x: X($0.date), y: Y($0.balance.double)) }, Theme.line, width: 2.5)
        let endValue: Double
        if !future.isEmpty, let today {
            pen.poly(future.map { CGPoint(x: X($0.date), y: Y($0.balance.double)) }, Theme.line, width: 2, dash: [4, 4])
            let now = past.last?.balance.double ?? future[0].balance.double
            let tx = X(today)
            pen.line(CGPoint(x: tx, y: mt), CGPoint(x: tx, y: h - 20), Theme.rule, dash: [3, 3])
            pen.dot(CGPoint(x: tx, y: Y(now)), 4.5, Theme.signal)
            pen.text(L("今天 {x}", ["x": amount(now)]), CGPoint(x: tx - 8, y: Y(now) - 12), .trailing, size: 12, weight: .semibold, color: Theme.ink)
            if let low = future.dropFirst().min(by: { $0.balance.double < $1.balance.double }) {
                let p = CGPoint(x: X(low.date), y: Y(low.balance.double))
                pen.ring(p, 4, Theme.muted)
                pen.text(L("最低 {x}，{d}", ["x": amount(low.balance), "d": Format.day(low.date)]), CGPoint(x: p.x - 6, y: p.y + 18), .trailing, size: 11.5)
            }
            endValue = future.last?.balance.double ?? now
        } else {
            endValue = past.last?.balance.double ?? 0
            if let last = past.last { pen.dot(CGPoint(x: X(last.date), y: Y(endValue)), 4, Theme.signal) }
        }
        pen.text(amount(endValue), CGPoint(x: w - mr + 52, y: Y(endValue)), size: 12, weight: .semibold, color: Theme.ink)

        // Daily flows around one baseline.
        let y0 = mt + balH + gap + flowH * 0.45
        let planned = plans.filter { !["card_payment", "transfer"].contains($0.kind) && $0.date >= start && $0.date <= end && !$0.overdue }
        let pastPeak: Double = past.map { max($0.inflow.double, $0.outflow.double) }.max() ?? 0
        let planPeak: Double = planned.map { abs($0.amount.double) }.max() ?? 0
        let peak = max(1, pastPeak, planPeak)
        let S = { (v: Double) in CGFloat((max(0, v) / peak).squareRoot()) * flowH * 0.5 }
        pen.line(CGPoint(x: ml, y: y0), CGPoint(x: w - mr, y: y0), Theme.chartAxis)
        pen.axis(L("进"), CGPoint(x: w - mr + 8, y: y0 - flowH * 0.3))
        pen.axis(L("出"), CGPoint(x: w - mr + 8, y: y0 + flowH * 0.35))
        let bw = max(3, (w - ml - mr) / CGFloat(span) * 0.6)
        for d in past {
            let x = X(d.date) - bw / 2
            if d.inflow.double > 0 { pen.rect(CGRect(x: x, y: y0 - S(d.inflow.double), width: bw, height: S(d.inflow.double)), Theme.line) }
            if d.outflow.double > 0 { pen.rect(CGRect(x: x, y: y0, width: bw, height: S(d.outflow.double)), Theme.line2) }
        }
        for e in planned {
            let a = e.amount.double
            let x = X(e.date) - bw / 2
            let r = a > 0 ? CGRect(x: x, y: y0 - S(a), width: bw, height: S(a)) : CGRect(x: x, y: y0, width: bw, height: S(-a))
            pen.box(r, Theme.muted)
        }
        // Name the days that matter: money coming in, and the biggest day out.
        let bigIn = max(past.map(\.inflow.double).max() ?? 0, planned.map(\.amount.double).max() ?? 0)
        func anchorFor(_ x: CGFloat) -> UnitPoint { x < ml + 30 ? .leading : x > w - mr - 30 ? .trailing : .center }
        for d in past where d.inflow.double >= bigIn * 0.3 && d.inflow.double > 0 {
            let x = X(d.date)
            pen.text(compactMoney(d.inflow.double, currency: currency), CGPoint(x: x, y: y0 - flowH * 0.5 - 8), anchorFor(x - bw / 2), size: 10.5)
        }
        for e in planned where e.amount.double >= bigIn * 0.3 {
            let x = X(e.date)
            pen.text(compactMoney(e.amount.double, currency: currency), CGPoint(x: x, y: y0 - flowH * 0.5 - 8), anchorFor(x), size: 10.5)
        }
        if let big = past.max(by: { $0.outflow.double < $1.outflow.double }), big.outflow.double > 0 {
            let x = X(big.date)
            pen.text(compactMoney(big.outflow.double, currency: currency), CGPoint(x: x, y: y0 + flowH * 0.5 + 10), anchorFor(x - bw / 2), size: 10.5)
        }
        var d = start
        while d <= end {
            if [1, 8, 15, 22, 29].contains(Day.dayOfMonth(d)) {
                let x = X(d)
                pen.axis("\(Int(d.dropFirst(5).prefix(2)) ?? 0)/\(Day.dayOfMonth(d))", CGPoint(x: x < ml + 20 ? ml : x, y: h - 6), x < ml + 20 ? .leading : .center)
            }
            d = Day.add(d, 1)
        }
    }
}

// MARK: - twelve months

/// Income and spending per month as paired bars (the last month's spending in the accent), with
/// the savings rate as its own small panel underneath: no second axis on the same plot.
struct MonthsChart: View {
    let series: [MonthSummary]
    /// A month was clicked (yyyy-MM): show it in 回顾.
    var onPickMonth: ((String) -> Void)?

    var currency = "USD"
    private func amount(_ value: Money) -> String { m0(value, currency: currency) }

    var body: some View {
        InteractiveCanvas(height: 340, label: L("近 12 个月收入、支出和储蓄率"), draw: draw, readout: readout, pick: pick)
    }

    private static let margins: (l: CGFloat, r: CGFloat, t: CGFloat) = (8, 60, 22)

    private func index(at p: CGPoint, _ size: CGSize) -> Int? {
        guard !series.isEmpty else { return nil }
        let m = Self.margins
        let slot = (size.width - m.l - m.r) / CGFloat(series.count)
        let i = Int(floor((p.x - m.l) / slot))
        return series.indices.contains(i) ? i : nil
    }

    private func readout(_ pen: Pen, _ size: CGSize, _ p: CGPoint) {
        guard let i = index(at: p, size) else { return }
        let m = Self.margins
        let slot = (size.width - m.l - m.r) / CGFloat(series.count)
        let x = m.l + (CGFloat(i) + 0.5) * slot
        pen.rect(CGRect(x: x - slot / 2, y: m.t, width: slot, height: size.height - m.t - 4), Theme.fill.opacity(0.35))
        let s = series[i]
        var lines: [(String, Bool)] = [(Format.month(s.month), true)]
        lines.append((L("收入 {x}", ["x": amount(s.income)]), false))
        lines.append((L("支出 {x}", ["x": amount(s.expenses)]), false))
        if let r = s.savingsRate { lines.append((L("储蓄率 {p}", ["p": Format.percent(r)]), true)) }
        if onPickMonth != nil { lines.append((L("点一下看这个月"), false)) }
        pen.tooltip(lines, x: x + slot / 2 - 12, top: m.t + 4, width: size.width)
    }

    private func pick(_ size: CGSize, _ p: CGPoint) {
        guard let i = index(at: p, size) else { return }
        onPickMonth?(series[i].month)
    }

    private func draw(_ pen: Pen, _ size: CGSize) {
        let s = series
        guard !s.isEmpty else { return }
        let (ml, mr, mt) = Self.margins
        let w = size.width, h = size.height
        let topH: CGFloat = 180, gap: CGFloat = 52
        let rateH = h - mt - topH - gap - 24
        let n = s.count
        let slot = (w - ml - mr) / CGFloat(n)
        let peak = s.map { max($0.income.double, $0.expenses.double) }.max() ?? 1
        let step = niceStep(peak, ticks: 5)
        let ymax = max(step, ceil(peak / step) * step)
        let Y = { (v: Double) in mt + CGFloat(1 - v / ymax) * topH }
        var v = 0.0
        while v <= ymax + 0.5 {
            pen.line(CGPoint(x: ml, y: Y(v)), CGPoint(x: w - mr, y: Y(v)), v == 0 ? Theme.chartAxis : Theme.chartGrid)
            pen.axis(axisUSD(v, step: step, currency: currency), CGPoint(x: w - mr + 8, y: Y(v)))
            v += step
        }
        let bw = min(14, slot * 0.3)
        for (i, m) in s.enumerated() {
            let cx = ml + (CGFloat(i) + 0.5) * slot
            let inc = max(0, m.income.double), exp = max(0, m.expenses.double)
            pen.rect(CGRect(x: cx - bw - 1, y: Y(inc), width: bw, height: Y(0) - Y(inc)), Theme.line2)
            pen.rect(CGRect(x: cx + 1, y: Y(exp), width: bw, height: Y(0) - Y(exp)), i == n - 1 ? Theme.signal : Theme.line)
            pen.axis(Format.month(m.month, short: true), CGPoint(x: cx, y: Y(0) + 12), .center)
        }
        if let last = s.last {
            let cx = ml + (CGFloat(n) - 0.5) * slot
            pen.text(L("收入"), CGPoint(x: cx - 1 - bw / 2, y: Y(last.income.double) - 9), .center, size: 11)
            pen.text(L("支出"), CGPoint(x: cx + 1 + bw / 2, y: Y(last.expenses.double) - 9), .center, size: 11, weight: .semibold, color: Theme.ink)
        }
        // Savings rate.
        let y0 = mt + topH + gap
        let rates = s.map { $0.savingsRate ?? 0 }
        let rmax = max(0.6, ceil(((rates.max() ?? 0) + 0.05) * 10) / 10)
        let rmin = min(0, floor((rates.min() ?? 0) * 10) / 10)
        let Yr = { (r: Double) in y0 + CGFloat(1 - (r - rmin) / (rmax - rmin)) * rateH }
        pen.text(L("储蓄率"), CGPoint(x: ml, y: y0 - 14), size: 11)
        for r in [rmin, (rmin + rmax) / 2, rmax] {
            pen.line(CGPoint(x: ml, y: Yr(r)), CGPoint(x: w - mr, y: Yr(r)), r == rmin ? Theme.chartAxis : Theme.chartGrid)
            pen.axis(String(format: "%.0f%%", r * 100), CGPoint(x: w - mr + 8, y: Yr(r)))
        }
        let pts = rates.enumerated().map { CGPoint(x: ml + (CGFloat($0.offset) + 0.5) * slot, y: Yr($0.element)) }
        pen.poly(pts, Theme.line, width: 1.75)
        if let lo = rates.indices.min(by: { rates[$0] < rates[$1] }), lo != n - 1 {
            pen.ring(pts[lo], 3.5, Theme.muted)
            pen.text(String(format: "%.0f%%", rates[lo] * 100), CGPoint(x: pts[lo].x, y: pts[lo].y + 13), .center, size: 11, mono: true)
        }
        if let p = pts.last, let r = rates.last {
            pen.dot(p, 3.5, Theme.signal)
            pen.text(String(format: "%.0f%%", r * 100), CGPoint(x: p.x, y: p.y - 11), .center, size: 11, weight: .semibold, color: Theme.ink, mono: true)
        }
    }
}

// MARK: - categories against usual

/// Each category's month beside its 12-month average: the amount, a bar for the difference
/// (right = more than usual, left = less) labelled with the difference, and the average.
struct CategoryVarianceChart: View {
    let items: [CategoryAmount]
    /// A category was clicked: show its transactions for the month.
    var onPickCategory: ((String) -> Void)?

    var body: some View {
        InteractiveCanvas(
            height: CGFloat(30 + 44 * items.count), label: L("各类花销和平时比较"), draw: draw,
            readout: readout, pick: pick
        )
    }

    private func row(at p: CGPoint) -> Int? {
        let i = Int(floor((p.y - 30) / 44))
        return items.indices.contains(i) ? i : nil
    }

    private func readout(_ pen: Pen, _ size: CGSize, _ p: CGPoint) {
        guard onPickCategory != nil, let i = row(at: p) else { return }
        pen.rect(CGRect(x: -6, y: 30 + CGFloat(i) * 44, width: size.width + 12, height: 44), Theme.fill.opacity(0.4))
    }

    private func pick(_ size: CGSize, _ p: CGPoint) {
        guard let i = row(at: p) else { return }
        onPickCategory?(items[i].category)
    }

    private func draw(_ pen: Pen, _ size: CGSize) {
        let w = size.width, h = size.height
        let nameW: CGFloat = 72, amtW: CGFloat = 64
        let x0 = nameW + amtW + 12
        let span = w - x0 - 60
        let zero = x0 + span / 2
        let half = max(20, span / 2 - 46)  // room for the difference beside its bar
        let dmax = max(1, items.map { abs($0.amount.double - $0.avg12.double) }.max() ?? 1)
        pen.axis(L("本月"), CGPoint(x: nameW + amtW, y: 10), .trailing)
        pen.axis(L("比平时"), CGPoint(x: zero, y: 10), .center)
        pen.axis(L("平时"), CGPoint(x: w, y: 10), .trailing)
        pen.line(CGPoint(x: zero, y: 22), CGPoint(x: zero, y: h - 4), Theme.chartAxis)
        for (i, c) in items.enumerated() {
            let y = 30 + CGFloat(i) * 44 + 22
            let a = c.amount.double, u = c.avg12.double, d = a - u
            let bw = max(1.5, CGFloat(abs(d) / dmax) * half)
            let up = u > 0 && d > u * 0.15
            pen.line(CGPoint(x: 0, y: y + 22), CGPoint(x: w, y: y + 22), Theme.chartGrid)
            pen.text(Format.account(c.category), CGPoint(x: 0, y: y), size: 12, color: Theme.ink)
            pen.text(m0(a), CGPoint(x: nameW + amtW, y: y), .trailing, size: 12, weight: .semibold, color: Theme.ink, mono: true)
            pen.rect(CGRect(x: d >= 0 ? zero : zero - bw, y: y - 7, width: bw, height: 14), up ? Theme.signal : Theme.line2)
            let label = (d >= 0 ? "+" : "−") + m0(abs(d))
            pen.text(
                label, CGPoint(x: d >= 0 ? zero + bw + 6 : zero - bw - 6, y: y), d >= 0 ? .leading : .trailing,
                size: 11.5, weight: up ? .semibold : .regular, color: up ? Theme.signal : Theme.muted, mono: true
            )
            pen.text(m0(u), CGPoint(x: w, y: y), .trailing, size: 11.5, color: Theme.muted, mono: true)
        }
    }
}

// MARK: - net worth

/// Month-end net worth in the chosen display currency.
struct NetWorthExhibitChart: View {
    let series: [NWPoint]
    var currency = "USD"

    var body: some View {
        InteractiveCanvas(height: 260, label: L("净资产") + " · " + currency, draw: draw, readout: readout)
    }

    private static let margins: (l: CGFloat, r: CGFloat, t: CGFloat, b: CGFloat) = (8, 170, 14, 26)

    private func readout(_ pen: Pen, _ size: CGSize, _ p: CGPoint) {
        let n = series.count
        guard n > 1 else { return }
        let m = Self.margins
        let f = (p.x - m.l) / (size.width - m.l - m.r)
        guard f >= -0.02, f <= 1.02 else { return }
        let i = min(n - 1, max(0, Int((f * CGFloat(n - 1)).rounded())))
        let x = m.l + CGFloat(i) / CGFloat(n - 1) * (size.width - m.l - m.r)
        pen.guide(x, from: m.t, to: size.height - m.b)
        let pt = series[i]
        let lines: [(String, Bool)] = [
            (Format.month(pt.month), true),
            (L("净资产 {x}", ["x": Format.money(pt.netWorth, currency: currency, digits: 0)]), true),
        ]
        pen.tooltip(lines, x: x, top: m.t + 4, width: size.width - m.r + 60)
    }

    private func amount(_ value: Double) -> String {
        Format.money(Decimal(value), currency: currency, digits: 0)
    }

    private func draw(_ pen: Pen, _ size: CGSize) {
        let pts = series
        guard pts.count > 1 else { return }
        let (ml, mr, mt, mb) = Self.margins
        let w = size.width, h = size.height
        let n = pts.count
        let nw = pts.map(\.netWorth.double)
        let peak = nw.max() ?? 0
        let trough = min(0, nw.min() ?? 0)
        let step = niceStep(max(0, peak) - trough, ticks: 4)
        let ymax = max(step, ceil(peak / step) * step)
        let ymin = floor(trough / step) * step
        let X = { (i: Int) in ml + CGFloat(i) / CGFloat(n - 1) * (w - ml - mr) }
        let Y = { (v: Double) in mt + CGFloat((ymax - v) / (ymax - ymin)) * (h - mt - mb) }
        var v = ymin
        while v <= ymax + 0.5 {
            pen.line(CGPoint(x: ml, y: Y(v)), CGPoint(x: w - mr, y: Y(v)), v == 0 ? Theme.chartAxis : Theme.chartGrid)
            pen.axis(axisUSD(v, step: step, currency: currency), CGPoint(x: w - mr + 8, y: Y(v)))
            v += step
        }
        let last = nw[n - 1]
        pen.poly((0..<n).map { CGPoint(x: X($0), y: Y(nw[$0])) }, Theme.line, width: 2)
        pen.dot(CGPoint(x: X(n - 1), y: Y(last)), 3.5, Theme.signal)
        pen.text(L("净资产 {x}", ["x": amount(last)]), CGPoint(x: w - mr + 52, y: Y(last) - 2),
                 size: 11.5, weight: .semibold, color: Theme.ink)
        let every = max(1, n / 4)
        for i in stride(from: 0, to: n, by: every) {
            pen.axis(pts[i].month.replacingOccurrences(of: "-", with: "."), CGPoint(x: X(i), y: h - 8), i == 0 ? .leading : .center)
        }
    }
}

// MARK: - tables

/// Lighthouses as a table: target, how far you are, years to go, and the year.
struct LighthouseTable: View {
    let voyage: Voyage
    var asOfYear: Int

    var body: some View {
        let now = max(0, voyage.investable.double)
        Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 0) {
            GridRow {
                head(L("灯塔"))
                head(L("目标资产")).gridColumnAlignment(.trailing)
                head(L("已经走到"))
                head(L("还要")).gridColumnAlignment(.trailing)
                head(L("约在")).gridColumnAlignment(.trailing)
            }
            .padding(.bottom, 6)
            Rectangle().fill(Theme.ink).frame(height: 1).gridCellUnsizedAxes(.horizontal)
            ForEach(voyage.lighthouses) { l in
                let p = l.target.double > 0 ? min(1, now / l.target.double) : 0
                GridRow {
                    Text(l.short).font(.ui(13)).foregroundStyle(Theme.ink).lineLimit(1)
                    Text(m0(l.target)).font(.mono(13)).foregroundStyle(Theme.ink)
                    HStack(spacing: 8) {
                        ZStack(alignment: .leading) {
                            Rectangle().fill(Theme.fill).frame(width: 72, height: 6)
                            Rectangle().fill(l.reached ? Theme.signal : Theme.line).frame(width: max(2, 72 * p), height: 6)
                        }
                        Text(Format.percent(p)).font(.mono(11)).foregroundStyle(Theme.muted)
                    }
                    Text(l.reached ? L("已到达") : l.yearsToGo.map { L("{n} 年", ["n": String(format: "%.1f", $0)]) } ?? L("到不了"))
                        .font(.mono(13)).foregroundStyle(Theme.ink)
                    Text(l.yearsToGo.map { String(asOfYear + Int($0.rounded())) } ?? "—")
                        .font(.mono(13)).foregroundStyle(Theme.muted)
                }
                .padding(.vertical, 11)
                Hairline().gridCellUnsizedAxes(.horizontal)
            }
        }
    }

    private func head(_ s: String) -> some View { Text(s).font(.ui(11)).foregroundStyle(Theme.muted) }
}

/// What is due and expected next, with the cash account's projected balance after each.
struct UpcomingTable: View {
    let events: [PulseEvent]
    let cash: [CashDay]

    var body: some View {
        let after = Dictionary(cash.map { ($0.date, $0.balance) }, uniquingKeysWith: { _, b in b })
        Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 0) {
            GridRow {
                head(L("日期"))
                head(L("项目"))
                head(L("金额")).gridColumnAlignment(.trailing)
                head(L("之后现金")).gridColumnAlignment(.trailing)
            }
            .padding(.bottom, 6)
            Rectangle().fill(Theme.ink).frame(height: 1).gridCellUnsizedAxes(.horizontal)
            ForEach(events) { e in
                GridRow(alignment: .firstTextBaseline) {
                    Text(Format.day(e.date)).font(.mono(11.5)).foregroundStyle(e.overdue ? Theme.signal : Theme.muted)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(eventName(e)).font(.ui(13)).foregroundStyle(Theme.ink).lineLimit(1)
                        Text(detail(e)).font(.ui(10.5)).foregroundStyle(Theme.muted).lineLimit(1)
                    }
                    Text(amount(e)).font(.mono(12.5)).foregroundStyle(e.amount.isNegative ? Theme.ink : Theme.inflow)
                    Text(after[e.date].map(m0) ?? "—").font(.mono(12)).foregroundStyle(Theme.muted)
                }
                .padding(.vertical, 9)
                Hairline().gridCellUnsizedAxes(.horizontal)
            }
        }
    }

    private func head(_ s: String) -> some View { Text(s).font(.ui(11)).foregroundStyle(Theme.muted) }

    private func amount(_ e: PulseEvent) -> String {
        let v = e.amount.value
        return Format.money(v, digits: abs(v) >= 100 ? 0 : 2, sign: !e.amount.isNegative)
    }

    private func detail(_ e: PulseEvent) -> String {
        var parts = [kindLabel(e.kind)]
        if e.estimated { parts.append(L("估计")) }
        if e.cadence == "annual" { parts.append(L("每年")) }
        if e.overdue { parts.append(L("还没看到")) }
        return parts.joined(separator: L("，"))
    }
}
