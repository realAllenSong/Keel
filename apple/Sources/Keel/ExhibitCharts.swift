import KeelCore
import SwiftUI

/// General-purpose exhibits for 账本 and 规划, in the same hand as 现在 and 回顾: right-hand value
/// axis, hairline grid, every series named at its end instead of in a legend, a hover readout.

struct ExhibitSeries {
    let label: String
    let values: [Double]  // .nan = no value at that point
    var color: Color = Theme.line
    var width: CGFloat = 2
    var dash: [CGFloat] = []
    var strong = true  // bold end label
}

struct ExhibitBand {
    let label: String
    let low: [Double]
    let high: [Double]
    var color: Color = Theme.band
}

/// A vertical dashed rule at an index (retirement, a life event).
struct ExhibitMark {
    let index: Int
    let label: String
}

/// A horizontal reference line (a floor, zero).
struct ExhibitRule {
    let value: Double
    let label: String
    var color: Color = Theme.signal
}

private func ticks(_ lo: Double, _ hi: Double, count: Int) -> (Double, Double, Double) {
    let step = niceStep(max(hi - lo, 1e-9), ticks: count)
    let a = floor(lo / step) * step, b = ceil(hi / step) * step
    return (a, b == a ? a + step : b, step)
}

private func nudge(_ labels: inout [(CGFloat, String, Bool, Color)], gap: CGFloat = 15) {
    labels.sort { $0.0 < $1.0 }
    for i in labels.indices.dropFirst() where labels[i].0 - labels[i - 1].0 < gap {
        labels[i].0 = labels[i - 1].0 + gap
    }
}

struct LineExhibitChart: View {
    let xLabels: [String]
    var series: [ExhibitSeries]
    var bands: [ExhibitBand] = []
    var marks: [ExhibitMark] = []
    var rules: [ExhibitRule] = []
    var format: (Double) -> String = { compactUSD($0) }
    var axis: (Double, Double) -> String = { axisUSD($0, step: $1) }
    var includeZero = false
    var height: CGFloat = 280
    var rightMargin: CGFloat = 150
    var onPick: ((Int) -> Void)?

    var body: some View {
        InteractiveCanvas(height: height, label: series.map(\.label).joined(separator: ", "), draw: draw, readout: readout, pick: pick)
    }

    private var n: Int { max(xLabels.count, series.map(\.values.count).max() ?? 0) }
    private var margins: (l: CGFloat, r: CGFloat, t: CGFloat, b: CGFloat) { (8, rightMargin, marks.isEmpty ? 12 : 26, 26) }

    private func domain() -> (Double, Double, Double)? {
        var vals = series.flatMap(\.values) + bands.flatMap { $0.low + $0.high } + rules.map(\.value)
        if includeZero { vals.append(0) }
        vals = vals.filter(\.isFinite)
        guard let lo = vals.min(), let hi = vals.max() else { return nil }
        return ticks(lo, hi, count: 5)
    }

    private func X(_ i: Int, _ w: CGFloat) -> CGFloat {
        let m = margins
        return m.l + CGFloat(i) / CGFloat(max(1, n - 1)) * (w - m.l - m.r)
    }

    private func index(at p: CGPoint, _ size: CGSize) -> Int? {
        let m = margins
        guard n > 1 else { return nil }
        let f = (p.x - m.l) / (size.width - m.l - m.r)
        guard f >= -0.02, f <= 1.02 else { return nil }
        return min(n - 1, max(0, Int((f * CGFloat(n - 1)).rounded())))
    }

    private func draw(_ pen: Pen, _ size: CGSize) {
        guard n > 1, let d = domain() else { return }
        let (lo, hi, step) = d
        let m = margins, w = size.width, h = size.height
        let Y = { (v: Double) in m.t + CGFloat(1 - (v - lo) / (hi - lo)) * (h - m.t - m.b) }
        var v = lo
        while v <= hi + step / 2 {
            pen.line(CGPoint(x: m.l, y: Y(v)), CGPoint(x: w - m.r, y: Y(v)), v == lo || (abs(v) < step / 2) ? Theme.chartAxis : Theme.chartGrid)
            pen.axis(axis(v, step), CGPoint(x: w - m.r + 8, y: Y(v)))
            v += step
        }
        for b in bands {
            let k = min(b.low.count, b.high.count)
            guard k > 1 else { continue }
            let top = (0..<k).map { CGPoint(x: X($0, w), y: Y(b.high[$0])) }
            let bottom = (0..<k).reversed().map { CGPoint(x: X($0, w), y: Y(b.low[$0])) }
            pen.area(top + bottom, b.color)
        }
        for r in rules {
            pen.line(CGPoint(x: m.l, y: Y(r.value)), CGPoint(x: w - m.r, y: Y(r.value)), r.color, dash: [4, 3])
            pen.text(r.label, CGPoint(x: m.l + 4, y: Y(r.value) - 9), size: 11, color: r.color)
        }
        for mk in marks where mk.index < n {
            let x = X(mk.index, w)
            pen.line(CGPoint(x: x, y: m.t - 4), CGPoint(x: x, y: h - m.b), Theme.rule, dash: [3, 3])
            pen.text(mk.label, CGPoint(x: x + 4, y: m.t - 14), size: 11)
        }
        var ends: [(CGFloat, String, Bool, Color)] = []
        for s in series {
            var run: [CGPoint] = []
            for (i, value) in s.values.enumerated() {
                if value.isFinite {
                    run.append(CGPoint(x: X(i, w), y: Y(value)))
                } else {
                    pen.poly(run, s.color, width: s.width, dash: s.dash)
                    run = []
                }
            }
            pen.poly(run, s.color, width: s.width, dash: s.dash)
            if let last = s.values.last(where: \.isFinite) {
                ends.append((Y(last), s.label + " " + format(last), s.strong, s.strong ? Theme.ink : Theme.muted))
            }
        }
        for b in bands {
            if let lo = b.low.last, let hi = b.high.last {
                ends.append(((Y(lo) + Y(hi)) / 2, b.label, false, Theme.muted))
            }
        }
        nudge(&ends)
        for (y, text, strong, color) in ends {
            pen.text(text, CGPoint(x: w - m.r + 52, y: y), size: 11.5, weight: strong ? .semibold : .regular, color: color)
        }
        // As many labels as fit (about 72 pt each), never fewer than two.
        let fit = max(2, Int((w - m.l - m.r) / 72))
        let every = max(1, Int((Double(n) / Double(fit)).rounded(.up)))
        for i in stride(from: 0, to: n, by: every) where i < xLabels.count {
            pen.axis(xLabels[i], CGPoint(x: X(i, w), y: h - 8), i == 0 ? .leading : .center)
        }
    }

    private func readout(_ pen: Pen, _ size: CGSize, _ p: CGPoint) {
        guard let i = index(at: p, size) else { return }
        let x = X(i, size.width)
        pen.guide(x, from: margins.t, to: size.height - margins.b)
        var lines: [(String, Bool)] = [(i < xLabels.count ? xLabels[i] : "\(i)", true)]
        for s in series where i < s.values.count && s.values[i].isFinite {
            lines.append((s.label + " " + format(s.values[i]), s.strong))
        }
        for b in bands where i < b.low.count && i < b.high.count {
            lines.append((b.label + " " + format(b.low[i]) + "–" + format(b.high[i]), false))
        }
        if onPick != nil { lines.append((L("点一下看这一点的明细"), false)) }
        pen.tooltip(lines, x: x, top: margins.t + 4, width: size.width - margins.r + 60)
    }

    private func pick(_ size: CGSize, _ p: CGPoint) {
        guard let i = index(at: p, size) else { return }
        onPick?(i)
    }
}

/// Bars per period: stacked series, or one series coloured by sign (gains and losses).
struct BarExhibitChart: View {
    let xLabels: [String]
    let series: [ExhibitSeries]  // stacked bottom to top; values ≥ 0 unless `bySign`
    var bySign = false  // one series: positive in the series colour, negative in the accent
    var format: (Double) -> String = { compactUSD($0) }
    var height: CGFloat = 240
    var rightMargin: CGFloat = 130

    var body: some View {
        InteractiveCanvas(height: height, label: series.map(\.label).joined(separator: ", "), draw: draw, readout: readout)
    }

    private var n: Int { series.map(\.values.count).max() ?? 0 }
    private var margins: (l: CGFloat, r: CGFloat, t: CGFloat, b: CGFloat) { (8, rightMargin, 12, 26) }

    private func totals() -> [(Double, Double)] {
        (0..<n).map { i in
            let vals = series.map { i < $0.values.count ? $0.values[i] : 0 }
            return (vals.filter { $0 < 0 }.reduce(0, +), vals.filter { $0 > 0 }.reduce(0, +))
        }
    }

    private func slot(_ w: CGFloat) -> CGFloat { (w - margins.l - margins.r) / CGFloat(max(1, n)) }

    private func draw(_ pen: Pen, _ size: CGSize) {
        guard n > 0 else { return }
        let t = totals()
        let (lo, hi, step) = ticks(min(0, t.map(\.0).min() ?? 0), max(0, t.map(\.1).max() ?? 1), count: 5)
        let m = margins, w = size.width, h = size.height
        let Y = { (v: Double) in m.t + CGFloat(1 - (v - lo) / (hi - lo)) * (h - m.t - m.b) }
        var v = lo
        while v <= hi + step / 2 {
            pen.line(CGPoint(x: m.l, y: Y(v)), CGPoint(x: w - m.r, y: Y(v)), abs(v) < step / 2 ? Theme.chartAxis : Theme.chartGrid)
            pen.axis(axisUSD(v, step: step), CGPoint(x: w - m.r + 8, y: Y(v)))
            v += step
        }
        let s = slot(w), bw = max(2, s * 0.62)
        for i in 0..<n {
            let x = m.l + CGFloat(i) * s + (s - bw) / 2
            var up = 0.0, down = 0.0
            for ser in series where i < ser.values.count {
                let value = ser.values[i]
                let base = value >= 0 ? up : down
                let top = base + value
                let color = bySign ? (value >= 0 ? ser.color : Theme.signal) : ser.color
                pen.rect(CGRect(x: x, y: min(Y(base), Y(top)), width: bw, height: abs(Y(top) - Y(base))), color)
                if value >= 0 { up = top } else { down = top }
            }
        }
        // Name the stack at its last bar.
        var ends: [(CGFloat, String, Bool, Color)] = []
        var base = 0.0
        for ser in series {
            guard let last = ser.values.last else { continue }
            ends.append((Y(base + last / 2), ser.label + " " + format(last), ser.strong, ser.strong ? Theme.ink : Theme.muted))
            base += max(0, last)
        }
        nudge(&ends)
        for (y, text, strong, color) in ends {
            pen.text(text, CGPoint(x: w - m.r + 48, y: y), size: 11.5, weight: strong ? .semibold : .regular, color: color)
        }
        let fit = max(2, Int((w - m.l - m.r) / 64))
        let every = max(1, Int((Double(n) / Double(fit)).rounded(.up)))
        for i in stride(from: 0, to: n, by: every) where i < xLabels.count {
            let x = m.l + (CGFloat(i) + 0.5) * s
            pen.axis(xLabels[i], CGPoint(x: i == 0 ? m.l : x, y: h - 8), i == 0 ? .leading : .center)
        }
    }

    private func readout(_ pen: Pen, _ size: CGSize, _ p: CGPoint) {
        let s = slot(size.width)
        let i = Int(floor((p.x - margins.l) / s))
        guard i >= 0, i < n else { return }
        pen.rect(CGRect(x: margins.l + CGFloat(i) * s, y: margins.t, width: s, height: size.height - margins.t - margins.b), Theme.ink.opacity(0.06))
        var lines: [(String, Bool)] = [(i < xLabels.count ? xLabels[i] : "\(i + 1)", true)]
        for ser in series where i < ser.values.count {
            lines.append((ser.label + " " + format(ser.values[i]), ser.strong))
        }
        pen.tooltip(lines, x: margins.l + (CGFloat(i) + 1) * s, top: margins.t + 4, width: size.width - margins.r + 60)
    }
}
