import AppKit
import Charts
import KeelCore
import SwiftUI

/// 持仓: what the brokerage accounts hold (value, cost, gain), the gains already realised, and the
/// trades themselves. Figures are the broker's own reports; nothing here changes the ledger.
struct HoldingsView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("keel.netWorthCurrency") private var currency = "USD"
    @State private var holdings = Loader<Holdings>("holdings.positions")
    @State private var realized = Loader<RealizedGains>("holdings.realized")
    @State private var trades = Loader<[PortfolioTrade]>("holdings.trades")
    @State private var performance = Loader<Performance>("holdings.performance")
    @State private var chart: Position?
    @State private var syncing = false
    @State private var allSales = false
    @State private var allTrades = false
    @State private var exporting = false

    private var shown: String { holdings.value?.currency ?? currency }
    private var year: String { String(model.today.prefix(4)) }

    var body: some View {
        VStack(alignment: .leading, spacing: 36) {
            if let error = holdings.error { ErrorLine(message: L("持仓暂时读不到：{e}", ["e": error])) }
            if let h = holdings.value {
                if h.accounts.allSatisfy(\.positions.isEmpty) && (trades.value ?? []).isEmpty {
                    empty
                } else {
                    head(h)
                    positions(h)
                    if let p = performance.value, p.since != nil { versus(p) }
                    if let r = realized.value { gains(r) }
                    if let t = trades.value, !t.isEmpty { tradeList(t) }
                }
            } else if holdings.error == nil {
                SkeletonRows(count: 6)
            }
        }
        .task(id: "\(model.revision)|\(currency)") { [currency] in
            guard let api = model.api else { return }
            do {
                async let h = api.holdings(currency: currency)
                async let r = api.realizedGains(currency: currency)
                async let t = api.portfolioTrades()
                let (hv, rv, tv) = try await (h, r, t)
                try Task.checkCancellation()
                holdings.set(hv)
                realized.set(rv)
                trades.set(tv)
            } catch {
                holdings.fail(error)
            }
            // Market quotes and index prices come from the network: after the page is up, and
            // never in its way. Quotes failing leaves the broker's own prices on screen.
            if let api = model.api, let live = try? await api.holdings(currency: currency, live: true),
               live.quotedAt != nil, !Task.isCancelled {
                holdings.set(live)
            }
            await performance.load { try await $0.performance(currency: currency) }
        }
        .sheet(item: $chart) { PositionChartSheet(position: $0).environment(model) }
    }

    // MARK: head

    private func head(_ h: Holdings) -> some View {
        let gain = h.gain?.value
        let done = realized.value?.byYear[year]?.value
        var sentence = Text(L("持仓市值 {v}", ["v": Format.money(h.invested, currency: shown)]))
        if let gain, gain != 0 {
            sentence = sentence + Text(L("，"))
                + Text(gain < 0
                    ? L("浮亏 {v}", ["v": Format.money(-gain, currency: shown)])
                    : L("浮盈 {v}", ["v": Format.money(gain, currency: shown)]))
                .foregroundColor(gain < 0 ? Theme.signal : Theme.ink)
        }
        if let done {
            sentence = sentence + Text(L("；今年已实现 {v}", ["v": Format.money(done, currency: shown, sign: true)]))
        }
        var facts: [String] = []
        if let quoted = h.quotedAt {
            facts.append(L("报价 {t}（延迟约 15 分钟）", ["t": Self.quoteTime(quoted)]))
        } else if let day = h.asOf {
            facts.append(L("截至 {d}", ["d": Format.day(day)]))
        }
        let held = h.accounts.filter { !$0.positions.isEmpty }.count
        facts.append(L("{n} 个券商账户", ["n": held]))
        if let change = h.dayChange?.value {
            facts.append(h.quotedAt != nil
                ? L("较券商 {d} 的报告 {v}", ["d": h.asOf.map { Format.day($0) } ?? "", "v": Format.money(change, currency: shown, digits: 2, sign: true)])
                : L("较上次报告 {v}", ["v": Format.money(change, currency: shown, digits: 2, sign: true)]))
        }
        if h.cash.value != 0 { facts.append(L("另有现金 {v}", ["v": Format.money(h.cash, currency: shown, digits: 2)])) }
        return VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .center, spacing: 16) {
                Text(facts.joined(separator: " · ")).font(.ui(13)).foregroundStyle(Theme.muted)
                Spacer(minLength: 16)
                LinkButton(title: syncing ? L("正在同步…") : L("同步券商"), arrow: false) { syncBrokers() }
                    .disabled(syncing)
                    .help(L("持仓数量和买卖记录来自券商，一般一天更新一次；这里可以马上再取一次。"))
                ReportCurrencyPicker(currency: $currency)
            }
            sentence.font(.serif(40)).foregroundStyle(Theme.ink).fixedSize(horizontal: false, vertical: true)
            if !h.missingFx.isEmpty { AssetMissingRates(pairs: h.missingFx) }
        }
    }

    /// "2026-11-20T19:55:00-05:00" -> "11月20日 19:55" in this Mac's time zone.
    static func quoteTime(_ iso: String) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        guard let date = f.date(from: iso) else { return String(iso.prefix(16)).replacingOccurrences(of: "T", with: " ") }
        let out = DateFormatter()
        out.dateFormat = "HH:mm"
        let day = DateFormatter()
        day.dateFormat = "yyyy-MM-dd"
        return Format.day(day.string(from: date)) + " " + out.string(from: date)
    }

    private func syncBrokers() {
        guard let api = model.api, !syncing else { return }
        syncing = true
        Task {
            defer { syncing = false }
            let result = try? await api.syncConnection("snaptrade")
            model.banner = result?.ok == true ? L("券商数据已更新") : L("同步没成功：{e}", ["e": result?.message ?? "—"])
            model.revision += 1
        }
    }

    private var empty: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(L("还没有持仓数据")).font(.serif(40)).foregroundStyle(Theme.ink)
            Text(L("在 设置 › 高级 里连接券商后，这里会列出每只股票和基金的数量、市值、成本和盈亏，以及买卖记录。"))
                .font(.ui(14)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            LinkButton(title: L("去设置")) { model.open(.settings) }
        }
    }

    // MARK: 01 positions

    private func positions(_ h: Holdings) -> some View {
        let total = h.value.value
        return VStack(alignment: .leading, spacing: 14) {
            SectionTitle(number: 1, title: L("持仓"))
            Grid(alignment: .trailing, horizontalSpacing: 20, verticalSpacing: 0) {
                GridRow {
                    Text(L("代码")).gridColumnAlignment(.leading)
                    Text(L("数量"))
                    Text(L("现价"))
                    Text(L("市值"))
                    Text(L("成本"))
                    Text(L("浮动盈亏"))
                    Text(L("占比")).gridColumnAlignment(.leading)
                }
                .font(.ui(11.5)).foregroundStyle(Theme.muted).padding(.bottom, 8)
                ForEach(h.accounts.filter { !$0.positions.isEmpty }) { acc in
                    GridRow {
                        Hairline(color: Theme.rule).gridCellColumns(7)
                    }
                    GridRow {
                        Text(Format.account(acc.account)).font(.ui(12)).foregroundStyle(Theme.muted)
                            .gridCellColumns(6).gridCellAnchor(.leading)
                        Text(Format.money(acc.value, currency: shown, digits: 2))
                            .font(.mono(13, .medium)).foregroundStyle(Theme.ink)
                            .gridCellAnchor(.trailing)
                    }
                    .padding(.top, 10).padding(.bottom, 4)
                    ForEach(acc.positions) { p in
                        GridRow {
                            if p.cashEquivalent || p.kind == "option" {
                                Text(p.cashEquivalent ? L("货币基金（现金）") : p.name)
                                    .font(.ui(13.5, .medium)).foregroundStyle(Theme.ink).lineLimit(1)
                            } else {
                                Button { chart = p } label: {
                                    Text(p.name).font(.ui(13.5, .medium)).foregroundStyle(Theme.ink).lineLimit(1)
                                        .underline(color: Theme.rule)
                                }
                                .buttonStyle(.plain).pointingHandCursor().help(L("看它的走势和你的买卖点"))
                            }
                            Text(p.quantity)
                            Text(p.price.map { Format.money(Money($0).value, currency: p.currency, digits: 2) } ?? "—")
                            Text(Format.money(p.value, currency: p.currency, digits: 2))
                            Text(p.cost.map { Format.money($0, currency: p.currency, digits: 2) } ?? "—")
                            Text(p.gain.map { Format.money($0, currency: p.currency, digits: 2, sign: true) } ?? "—")
                            Share(part: total == 0 ? 0 : NSDecimalNumber(decimal: p.value.value / total).doubleValue)
                        }
                        .font(.mono(13)).foregroundStyle(Theme.ink).padding(.vertical, 6)
                    }
                }
            }
        }
        .flatSection()
    }

    /// A position's share of everything held: an ink bar and the figure.
    private struct Share: View {
        let part: Double

        var body: some View {
            HStack(spacing: 10) {
                Rectangle().fill(Theme.ink).frame(width: max(2, 110 * min(1, max(0, part))), height: 3)
                Spacer(minLength: 0)
                Text(Format.percent(part, digits: part < 0.1 ? 1 : 0))
                    .font(.mono(11.5)).foregroundStyle(Theme.muted)
            }
            .frame(minWidth: 170, maxWidth: .infinity)
        }
    }

    // MARK: 02 realised gains

    private func gains(_ r: RealizedGains) -> some View {
        let sales = r.sales.filter { $0.date.hasPrefix(year) }
        let rows = allSales ? sales : Array(sales.prefix(6))
        let unknown = r.unmatched.filter { $0.date.hasPrefix(year) }.count
        return VStack(alignment: .leading, spacing: 14) {
            SectionTitle(number: 3, title: L("已实现盈亏"))
            HStack(alignment: .top, spacing: 40) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(Format.money(r.byYear[year]?.value ?? 0, currency: r.currency, digits: 2, sign: true))
                        .font(.display(44)).foregroundStyle(Theme.ink)
                    Text(L("{y} 年已实现 · {n} 次卖出", ["y": year, "n": sales.count]))
                        .font(.ui(12)).foregroundStyle(Theme.muted)
                    Text(L("按先买先卖计算。")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                    if let t = r.tax?[year] {
                        VStack(alignment: .leading, spacing: 3) {
                            taxRow(L("短期（持有一年内）"), t.shortTerm, r.currency)
                            taxRow(L("长期（持有超一年）"), t.longTerm, r.currency)
                            if t.washDisallowed.value != 0 { taxRow(L("其中洗售不能抵扣的亏损"), t.washDisallowed, r.currency) }
                        }
                        .padding(.top, 8)
                        Text(L("报税口径是估算，以券商的 1099-B 为准。")).font(.ui(11)).foregroundStyle(Theme.muted)
                    }
                    if unknown > 0 {
                        Text(L("另有 {n} 笔卖出的买入早于券商提供的记录，成本未知，没有计入。", ["n": unknown]))
                            .font(.ui(11.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(width: 250, alignment: .leading)
                if sales.isEmpty {
                    Text(L("今年还没有卖出。")).font(.ui(13)).foregroundStyle(Theme.muted)
                    Spacer(minLength: 0)
                } else {
                    VStack(alignment: .leading, spacing: 10) {
                        Grid(alignment: .trailing, horizontalSpacing: 20, verticalSpacing: 0) {
                            GridRow {
                                Text(L("日期")).gridColumnAlignment(.leading)
                                Text(L("代码")).gridColumnAlignment(.leading)
                                Text(L("数量"))
                                Text(L("成本"))
                                Text(L("卖出所得"))
                                Text(L("盈亏"))
                            }
                            .font(.ui(11.5)).foregroundStyle(Theme.muted).padding(.bottom, 8)
                            GridRow { Hairline(color: Theme.rule).gridCellColumns(6) }
                            ForEach(rows) { s in
                                GridRow {
                                    Text(Format.day(s.date)).foregroundStyle(Theme.muted)
                                    Text(s.name + ((s.legs ?? 1) > 1 ? " · " + L("{n} 腿期权", ["n": s.legs ?? 1]) : ""))
                                        .font(.ui(13.5, .medium)).lineLimit(1)
                                    Text((s.legs ?? 1) > 1 ? "—" : s.quantity)
                                    Text(Format.money(s.cost, currency: s.currency, digits: 2))
                                    Text(Format.money(s.proceeds, currency: s.currency, digits: 2))
                                    Text(Format.money(s.gain, currency: s.currency, digits: 2, sign: true))
                                }
                                .font(.mono(13)).foregroundStyle(Theme.ink).padding(.vertical, 6)
                            }
                        }
                        if sales.count > 6 {
                            LinkButton(title: allSales ? L("收起") : L("显示全部 {n} 次", ["n": sales.count]), arrow: false) {
                                allSales.toggle()
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .flatSection()
    }

    private func taxRow(_ title: String, _ amount: Money, _ currency: String) -> some View {
        HStack {
            Text(title).font(.ui(11.5)).foregroundStyle(Theme.muted)
            Spacer()
            Text(Format.money(amount, currency: currency, digits: 2, sign: true)).font(.mono(11.5)).foregroundStyle(Theme.ink)
        }
    }

    // MARK: 02 against an index

    private func versus(_ p: Performance) -> some View {
        let mine = p.gain.value
        let rows: [(String, Decimal, Double?)] = [(L("你的买卖"), mine, p.rate)] + p.versus.map { (L("同样的钱买 {t}", ["t": $0.ticker]), $0.gain.value, $0.rate) }
        let scale = rows.map { abs(NSDecimalNumber(decimal: $0.1).doubleValue) }.max() ?? 1
        let dividends = p.dividends.values.reduce(Decimal(0)) { $0 + $1.value }
        return VStack(alignment: .leading, spacing: 14) {
            SectionTitle(number: 2, title: L("和大盘比"))
            HStack(alignment: .top, spacing: 40) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(Format.money(mine, currency: p.currency, digits: 2, sign: true))
                        .font(.display(44)).foregroundStyle(mine < 0 ? Theme.signal : Theme.ink)
                    Text(L("{d} 以来，已实现加浮动", ["d": p.since.map { Format.day($0) + " " + $0.prefix(4) } ?? ""]))
                        .font(.ui(12)).foregroundStyle(Theme.muted)
                    VStack(alignment: .leading, spacing: 3) {
                        taxRow(L("投进去"), Money("-" + p.invested.raw), p.currency)
                        taxRow(L("卖出拿回"), p.returned, p.currency)
                        taxRow(L("还持有"), p.value, p.currency)
                        if dividends != 0 { taxRow(L("另收分红"), Money("\(dividends)"), p.currency) }
                    }
                    .padding(.top, 8)
                }
                .frame(width: 250, alignment: .leading)
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(rows, id: \.0) { title, gain, rate in
                        let part = scale == 0 ? 0 : abs(NSDecimalNumber(decimal: gain).doubleValue) / scale
                        HStack(spacing: 14) {
                            Text(title).font(.ui(13)).foregroundStyle(Theme.ink).frame(width: 150, alignment: .leading)
                            GeometryReader { geo in
                                Rectangle().fill(gain < 0 ? Theme.line2 : Theme.ink)
                                    .frame(width: max(2, geo.size.width * part), height: 5)
                                    .frame(maxHeight: .infinity, alignment: .center)
                            }
                            .frame(height: 18)
                            Text(Format.money(gain, currency: p.currency, digits: 2, sign: true))
                                .font(.mono(13)).foregroundStyle(Theme.ink).frame(width: 110, alignment: .trailing)
                            Text(rate.map { L("年化 {r}", ["r": Format.percent($0, digits: 1)]) } ?? "—")
                                .font(.mono(11.5)).foregroundStyle(Theme.muted).frame(width: 96, alignment: .trailing)
                        }
                    }
                    Text(p.versus.isEmpty
                        ? L("指数价格暂时取不到，稍后再看。")
                        : L("指数那几行：你每次买入，就假设同一天用同样多的钱买指数；你每次卖出，也卖掉同样多钱的指数。券商记录开始之前就持有的股票不计入。"))
                        .font(.ui(11.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .flatSection()
    }

    // MARK: 03 trades

    private func tradeList(_ all: [PortfolioTrade]) -> some View {
        let rows = allTrades ? all : Array(all.prefix(8))
        return VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                SectionTitle(number: 4, title: L("交易记录"))
                Spacer()
                LinkButton(title: L("导出持仓和交易"), arrow: false) { exporting = true }
                    .popover(isPresented: $exporting, arrowEdge: .bottom) { ExportChoices(currency: shown) { exporting = false } }
            }
            Grid(alignment: .trailing, horizontalSpacing: 20, verticalSpacing: 0) {
                GridRow {
                    Text(L("日期")).gridColumnAlignment(.leading)
                    Text(L("类型")).gridColumnAlignment(.leading)
                    Text(L("代码")).gridColumnAlignment(.leading)
                    Text(L("账户")).gridColumnAlignment(.leading)
                    Text(L("数量"))
                    Text(L("价格"))
                    Text(L("金额"))
                }
                .font(.ui(11.5)).foregroundStyle(Theme.muted).padding(.bottom, 8)
                GridRow { Hairline(color: Theme.rule).gridCellColumns(7) }
                ForEach(rows) { t in
                    let moved = t.amount.value != 0
                    GridRow {
                        Text(Format.day(t.date) + (t.date.hasPrefix(year) ? "" : " " + String(t.date.prefix(4)))).foregroundStyle(Theme.muted)
                        Text(Self.action(t.action)).font(.ui(13))
                        Text(t.name).font(.ui(13.5, .medium)).lineLimit(1)
                        Text(Format.account(t.account)).font(.ui(12)).foregroundStyle(Theme.muted).lineLimit(1)
                        Text(t.quantity.hasPrefix("-") ? String(t.quantity.dropFirst()) : t.quantity)
                        Text(t.price.flatMap { Money($0).value == 0 ? nil : Format.money(Money($0).value, currency: t.currency, digits: 2) } ?? "—")
                        Text(moved ? Format.money(t.amount, currency: t.currency, digits: 2, sign: true) : "—")
                    }
                    .font(.mono(13)).foregroundStyle(Theme.ink).padding(.vertical, 6)
                }
            }
            if all.count > 8 {
                LinkButton(title: allTrades ? L("收起") : L("显示全部 {n} 笔", ["n": all.count]), arrow: false) { allTrades.toggle() }
            }
        }
        .flatSection()
    }

    static func action(_ code: String) -> String {
        switch code {
        case "BUY": return L("买入")
        case "SELL": return L("卖出")
        case "REINVEST": return L("分红再投资")
        case "SPLIT": return L("拆股")
        case "STOCK_DIVIDEND": return L("送股")
        case "ASSIGNMENT": return L("期权被行权")
        case "EXERCISE": return L("期权行权")
        case "EXPIRATION": return L("期权到期")
        default: return code
        }
    }
}

/// Which period to export: accounts, positions at its end, trades inside it, as one CSV file
/// (what an employer's brokerage-account certification asks for).
private struct ExportChoices: View {
    @Environment(AppModel.self) private var model
    let currency: String
    let done: () -> Void

    private var choices: [(String, String, String)] {
        let today = model.today
        let parts = today.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return [] }
        let (y, m) = (parts[0], parts[1])
        let q = (m - 1) / 3  // 0...3
        func day(_ year: Int, _ month: Int, last: Bool) -> String {
            guard last else { return String(format: "%04d-%02d-01", year, month) }
            var c = DateComponents()
            c.year = month == 12 ? year + 1 : year
            c.month = month == 12 ? 1 : month + 1
            c.day = 0  // the day before the first of next month
            let cal = Calendar(identifier: .gregorian)
            let d = cal.date(from: c).map { cal.dateComponents([.year, .month, .day], from: $0) }
            return String(format: "%04d-%02d-%02d", d?.year ?? year, d?.month ?? month, d?.day ?? 28)
        }
        let (py, pq) = q == 0 ? (y - 1, 3) : (y, q - 1)
        return [
            (L("上个季度"), day(py, pq * 3 + 1, last: false), day(py, pq * 3 + 3, last: true)),
            (L("本季度到今天"), day(y, q * 3 + 1, last: false), today),
            (L("今年到今天"), day(y, 1, last: false), today),
            (L("去年全年"), day(y - 1, 1, last: false), day(y - 1, 12, last: true)),
        ]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(L("导出哪一段时间？")).font(.ui(12)).foregroundStyle(Theme.muted).padding(.bottom, 6)
            ForEach(choices, id: \.0) { title, start, end in
                Button {
                    done()
                    export(start: start, end: end)
                } label: {
                    HStack {
                        Text(title).font(.ui(13)).foregroundStyle(Theme.ink)
                        Spacer(minLength: 24)
                        Text(start + " – " + end).font(.mono(11)).foregroundStyle(Theme.muted)
                    }
                    .padding(.vertical, 5).contentShape(Rectangle())
                }
                .buttonStyle(.plain).pointingHandCursor()
            }
            Text(L("一份 CSV：账户、期末持仓、期间的买卖。完整账号和开户日期请以券商为准。"))
                .font(.ui(11)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                .padding(.top, 8)
        }
        .padding(16).frame(width: 340)
    }

    private func export(start: String, end: String) {
        guard let api = model.api else { return }
        Task {
            do {
                let csv = try await api.brokerageStatementCSV(start: start, end: end, currency: currency)
                let panel = NSSavePanel()
                panel.nameFieldStringValue = "Keel-holdings-\(start)-\(end).csv"
                panel.allowedContentTypes = [.commaSeparatedText]
                guard panel.runModal() == .OK, let url = panel.url else { return }
                try csv.write(to: url, atomically: true, encoding: .utf8)
                model.banner = L("已导出到 {f}", ["f": url.lastPathComponent])
            } catch {
                model.banner = L("导出失败：{e}", ["e": error.localizedDescription])
            }
        }
    }
}

/// One holding since its first trade: the daily close, with each buy and sale marked on the line.
private struct PositionChartSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let position: Position
    @State private var history = Loader<SymbolHistory>()

    private static let parser: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .firstTextBaseline) {
                Text(position.name).font(.display(26)).foregroundStyle(Theme.ink)
                Text(L("持有 {q}，成本 {c}", [
                    "q": position.quantity,
                    "c": position.cost.map { Format.money($0, currency: position.currency, digits: 2) } ?? "—",
                ])).font(.ui(12.5)).foregroundStyle(Theme.muted)
                Spacer()
                Button(L("关闭")) { dismiss() }.buttonStyle(.keelOutlineCompact).keyboardShortcut(.cancelAction)
            }
            if let h = history.value, !h.closes.isEmpty {
                let points = h.closes.compactMap { p in Self.parser.date(from: p.day).map { ($0, p.close) } }
                let byDay = Dictionary(h.closes.map { ($0.day, $0.close) }, uniquingKeysWith: { a, _ in a })
                Chart {
                    ForEach(points, id: \.0) { day, close in
                        LineMark(x: .value("day", day), y: .value("close", close))
                            .foregroundStyle(Theme.line).lineStyle(StrokeStyle(lineWidth: 1.2))
                    }
                    ForEach(h.trades.filter { ["BUY", "SELL", "REINVEST"].contains($0.action) }) { t in
                        // On the line at that day's close: the chart is split-adjusted, the trade price is not.
                        if let day = Self.parser.date(from: t.date), let close = byDay[t.date] ?? nearest(byDay, t.date) {
                            PointMark(x: .value("day", day), y: .value("close", close))
                                .symbol(t.action == "SELL" ? .circle : .circle)
                                .symbolSize(46)
                                .foregroundStyle(t.action == "SELL" ? Theme.signal : Theme.ink)
                        }
                    }
                }
                .chartYScale(domain: .automatic(includesZero: false))
                .frame(height: 320)
                HStack(spacing: 16) {
                    legend(Theme.ink, L("买入"))
                    legend(Theme.signal, L("卖出"))
                    Spacer()
                    Text(L("价格已按拆股和分红调整，所以和当时的成交价可能不同。")).font(.ui(11)).foregroundStyle(Theme.muted)
                }
            } else if history.value != nil || history.error != nil {
                Text(L("这只的历史价格暂时取不到。")).font(.ui(13)).foregroundStyle(Theme.muted).frame(height: 320)
            } else {
                SkeletonRows(count: 4).frame(height: 320)
            }
        }
        .padding(24).frame(width: 860).background(Theme.paper)
        .task { await history.load { try await $0.symbolHistory(position.symbol) } }
    }

    private func nearest(_ closes: [String: Double], _ day: String) -> Double? {
        closes.keys.filter { $0 <= day }.max().flatMap { closes[$0] }
    }

    private func legend(_ color: Color, _ text: String) -> some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(text).font(.ui(11.5)).foregroundStyle(Theme.muted)
        }
    }
}
