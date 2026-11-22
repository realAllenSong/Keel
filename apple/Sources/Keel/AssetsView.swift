import KeelCore
import SwiftUI

/// 资产: what I own minus what I owe at the end of the chosen month, account by account. Accounts
/// with statements can be checked against the statement balance; the rest take a valuation.
struct AssetsView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("keel.netWorthCurrency") private var currency = "USD"
    var selectedMonth: String? = nil
    private var periodMonth: String? { selectedMonth ?? model.month }
    private var date: String? { periodMonth.map { ReportingPeriod.end(of: $0, through: model.today) } }
    @State private var sheet = Loader<BalanceSheet>("assets.sheet")
    @State private var series = Loader<[NWPoint]>("assets.series")
    @State private var accounts = Loader<[LedgerAccount]>("assets.accounts")
    @State private var statements: (month: String, accounts: Set<String>)?
    @State private var adding = false

    private var balanceSheet: BalanceSheet? {
        guard let bs = sheet.value, bs.date == date else { return nil }
        return bs
    }

    /// The currency of what is on screen (the picker may already say the next one).
    private var shown: String { balanceSheet?.currency ?? currency }

    private var netWorthSeries: [NWPoint] {
        guard let points = series.value, points.last?.month == periodMonth,
              points.allSatisfy({ ($0.currency ?? "USD") == shown }) else { return [] }
        return points
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 40) {
            header
            if let error = sheet.error { ErrorLine(message: L("资产暂时读不到：{e}", ["e": error])) }
            if let bs = balanceSheet {
                let assetGroups = groups(bs)
                VStack(alignment: .leading, spacing: 32) {
                    // Dollars and renminbi first, each with its own categories and debts inside.
                    ForEach(currencySections(assetGroups), id: \.code) { section in
                        VStack(alignment: .leading, spacing: 28) {
                            CurrencyHeader(code: section.code, groups: section.groups)
                            ForEach(section.groups, id: \.key) { group in
                                AssetGroup(
                                    key: group.key, rows: group.rows, date: bs.date,
                                    observations: bs.observedOn ?? [:],
                                    commodity: nativeCommodity,
                                    statement: statements?.month == periodMonth ? statements?.accounts : nil
                                )
                            }
                        }
                        .padding(.bottom, 12)
                    }
                    if assetGroups.isEmpty {
                        Text(L("还没有账户。拖入账单，或记下一项资产的估值。")).font(.ui(13)).foregroundStyle(Theme.muted)
                    }
                    if let flow = accounts.value?.filter({ $0.flowOnly == true }), !flow.isEmpty {
                        Text(L("{names}只记收入和支出，不计入净资产：导出的账单里没有余额，付款大多来自绑定的银行卡或花呗。", [
                            "names": flow.map { Format.account($0.name) }.joined(separator: L("、")),
                        ]))
                        .font(.ui(11.5)).foregroundStyle(Theme.muted)
                    }
                    VStack(alignment: .leading, spacing: 0) {
                        LinkButton(title: L("添加没有账单的资产"), arrow: false) { adding.toggle() }
                        if adding { AccountEditor(account: nil, commodity: "USD", date: bs.date, statement: false) }
                    }
                }
            } else if sheet.error == nil {
                SkeletonRows(count: 6)
            }
            let points = netWorthSeries
            if !points.isEmpty {
                let missing = Set(points.flatMap { $0.missingFx ?? [] }).sorted()
                VStack(alignment: .leading, spacing: 6) {
                    Hairline().padding(.bottom, 20)
                    Text(L("一路以来的积累")).font(.display(21)).foregroundStyle(Theme.ink)
                    Text(L("按月末汇率折算的净资产。")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                    if missing.isEmpty {
                        NetWorthExhibitChart(series: points, currency: shown).padding(.top, 12)
                    } else {
                        AssetMissingRates(pairs: missing).padding(.top, 12)
                    }
                }
            }
        }
        .task(id: "\(model.revision)|\(date ?? "")|\(currency)") { [periodMonth, date, currency] in
            guard let month = periodMonth, let date else { return }
            // Balance sheet and history swap together: a currency switch updates in place.
            async let c: Void = accounts.load { try await $0.accounts() }
            if let api = model.api {
                do {
                    async let bs = api.balanceSheet(date: date, currency: currency)
                    async let points = api.networth(end: month, currency: currency)
                    let (b, p) = try await (bs, points)
                    try Task.checkCancellation()
                    sheet.set(b)
                    series.set(p)
                } catch {
                    sheet.fail(error)
                }
            }
            _ = await c
            if let api = model.api, !Task.isCancelled {
                async let files = try? api.imports()
                async let list = try? api.checklist(month: month)
                var names = Set((await files ?? []).compactMap(\.account))
                (await list ?? []).forEach { names.insert($0.account) }
                if !Task.isCancelled { statements = (month, names) }
            }
        }
    }

    private var header: some View {
        let bs = balanceSheet
        let missing = bs?.missingFx ?? []
        let previous = netWorthSeries.dropLast().last
        let change: Decimal? = bs.flatMap { b in
            previous.flatMap { p in
                missing.isEmpty && (p.missingFx ?? []).isEmpty ? b.netWorth.value - p.netWorth.value : nil
            }
        }
        let netWorth = bs.map { missing.isEmpty ? Format.money($0.netWorth, currency: shown, digits: 2) : "—" } ?? " "
        return VStack(alignment: .leading, spacing: 10) {
            Text(L("账本净资产")).font(.ui(14)).foregroundStyle(Theme.muted)
            Text(netWorth).font(.display(50)).foregroundStyle(Theme.ink)
            if let sheet = balanceSheet {
                HStack(alignment: .top, spacing: 20) {
                    summary(L("资产合计"), totals: NativeBalances.total(sheet.lines.filter { $0.type == "Assets" }))
                    // Positive = owed; a negative figure is a card overpaid.
                    summary(L("欠款余额"), totals: NativeBalances.total(sheet.lines.filter { $0.type == "Liabilities" }).mapValues { -$0 })
                }
            }
            if let change {
                (Text(change >= 0 ? L("比上月多") : L("比上月少")) + Text(" ")
                    + Text(Format.money(abs(change), currency: shown, digits: 2)).font(.mono(13)).foregroundColor(Theme.ink)
                    + Text(L(" · 可能含补录资产及汇率变化")))
                    .font(.ui(13)).foregroundStyle(Theme.muted)
            } else {
                Text(periodMonth == model.currentMonth ? L("资产减去负债，按截至今天的最新数据计算。") : L("资产减去负债，按所选月末计算。"))
                    .font(.ui(13)).foregroundStyle(Theme.muted)
            }
            if !missing.isEmpty { AssetMissingRates(pairs: missing) }
        }
    }

    private func summary(_ label: String, totals: [String: Decimal]) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(label).font(.ui(13))
            NativeBalances(amounts: totals).font(.mono(13))
        }.foregroundStyle(Theme.muted)
    }

    private func nativeCommodity(_ name: String) -> String? {
        let matches = accounts.value?.filter { $0.name == name } ?? []
        let commodities = Set(matches.map(\.commodity))
        if commodities.count == 1 { return commodities.first }
        // Multi-commodity accounts need a declared currency before they can be edited here.
        guard let declared = matches.first?.currency, commodities.contains(declared) else { return nil }
        return declared
    }

    struct Group {
        let key: String
        let rows: [BalanceLine]
    }

    struct CurrencySection {
        let code: String
        let groups: [Group]
    }

    /// The account's own currency: declared on the account, else its single balance commodity.
    private func currencyOf(_ line: BalanceLine) -> String {
        if let declared = accounts.value?.first(where: { $0.name == line.account })?.currency { return declared }
        if let only = line.originalBalances?.keys, only.count == 1, let code = only.first { return code }
        return nativeCommodity(line.account) ?? "USD"
    }

    /// The same category groups, split by currency: USD, then CNY, then any other.
    private func currencySections(_ groups: [Group]) -> [CurrencySection] {
        var codes: [String] = []
        for g in groups { for r in g.rows where !codes.contains(currencyOf(r)) { codes.append(currencyOf(r)) } }
        let rank = { (c: String) in c == "USD" ? 0 : c == "CNY" ? 1 : 2 }
        codes.sort { (rank($0), $0) < (rank($1), $1) }
        return codes.map { code in
            CurrencySection(code: code, groups: groups.compactMap { g in
                let rows = g.rows.filter { currencyOf($0) == code }
                return rows.isEmpty ? nil : Group(key: g.key, rows: rows)
            })
        }
    }

    /// Asset classes in the balance sheet's order, then debts. Accounts without a balance this month
    /// still get a row, so their statement can be checked or a value recorded.
    private func groups(_ bs: BalanceSheet) -> [Group] {
        var rows = bs.lines
        for a in accounts.value ?? [] where (a.type == "Assets" || a.type == "Liabilities") && a.flowOnly != true {
            if !rows.contains(where: { $0.account == a.name }) {
                rows.append(BalanceLine(account: a.name, type: a.type, assetClass: a.assetClass, institution: a.institution, balance: Money("0")))
            }
        }
        func cls(_ l: BalanceLine) -> String { Self.classKey(l.assetClass ?? "other") }
        var keys = bs.byClass.keys.map(Self.classKey)
        for l in rows where l.type == "Assets" && !keys.contains(cls(l)) { keys.append(cls(l)) }
        keys.sort { (Self.order.firstIndex(of: $0) ?? 99) < (Self.order.firstIndex(of: $1) ?? 99) }
        var out: [Group] = keys.compactMap { key in
            let group = rows.filter { $0.type == "Assets" && cls($0) == key }
            guard !group.isEmpty else { return nil }
            return Group(key: key, rows: group)
        }
        let debts = rows.filter { $0.type == "Liabilities" }
        if !debts.isEmpty { out.append(Group(key: "liabilities", rows: debts)) }
        return out
    }

    static let order = ["cash", "term_deposit", "brokerage", "retirement", "crypto", "realEstate", "vehicle", "other"]
    static func classKey(_ raw: String) -> String { raw == "real_estate" ? "realEstate" : raw }

    static func monthEnd(_ month: String) -> String {
        let p = month.split(separator: "-").compactMap { Int($0) }
        guard p.count == 2 else { return month }
        var comps = DateComponents(year: p[0], month: p[1] + 1, day: 0)
        comps.calendar = Calendar(identifier: .gregorian)
        let day = comps.calendar?.date(from: comps).map { Calendar(identifier: .gregorian).component(.day, from: $0) } ?? 28
        return String(format: "%04d-%02d-%02d", p[0], p[1], day)
    }
}

private struct AssetGroup: View {
    let key: String
    let rows: [BalanceLine]
    let date: String
    let observations: [String: String]
    let commodity: (String) -> String?
    let statement: Set<String>?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                HStack(spacing: 8) {
                    Dot(color: color)
                    Text(title).font(.display(21)).foregroundStyle(Theme.ink)
                }
                Spacer()
                NativeBalances(amounts: NativeBalances.total(rows))
                    .font(.mono(13.5)).foregroundStyle(Theme.ink)
            }
            .padding(.bottom, 12)
            Hairline()
            ForEach(rows) { line in
                AccountRow(line: line, date: date,
                           observedOn: observations[line.account],
                           commodity: commodity(line.account), statement: statement)
            }
        }
    }

    private var title: String {
        if key == "liabilities" { return L("欠款") }
        return NetworthChart.classes.first { $0.key == key }?.label ?? key
    }

    private var color: Color {
        if key == "liabilities" { return Theme.series[6] }
        return Theme.series[NetworthChart.classes.first { $0.key == key }?.slot ?? 6]
    }
}

/// "美元资产": what you hold in this currency, what you owe in it, and the difference, all in
/// the currency itself (no conversion).
private struct CurrencyHeader: View {
    let code: String
    let groups: [AssetsView.Group]

    var body: some View {
        let assets = NativeBalances.total(groups.filter { $0.key != "liabilities" }.flatMap(\.rows))[code] ?? 0
        // Owed on cards and loans; negative when a card was overpaid (the bank owes you).
        let debts = -(NativeBalances.total(groups.filter { $0.key == "liabilities" }.flatMap(\.rows))[code] ?? 0)
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(title).font(.display(28)).foregroundStyle(Theme.ink)
                Spacer()
                Text(Format.money(assets - debts, currency: code, digits: 2)).font(.display(28)).foregroundStyle(Theme.ink)
            }
            Text(debts == 0
                ? L("资产 {a}", ["a": Format.money(assets, currency: code, digits: 2)])
                : debts > 0
                    ? L("资产 {a} − 欠款 {d}", ["a": Format.money(assets, currency: code, digits: 2), "d": Format.money(debts, currency: code, digits: 2)])
                    : L("资产 {a} + 信用卡多还 {d}", ["a": Format.money(assets, currency: code, digits: 2), "d": Format.money(-debts, currency: code, digits: 2)]))
                .font(.mono(12.5)).foregroundStyle(Theme.muted)
        }
        .padding(.bottom, 10)
        .overlay(alignment: .bottom) { Rectangle().fill(Theme.ink).frame(height: 1.5) }
    }

    private var title: String {
        switch code {
        case "USD": L("美元资产")
        case "CNY": L("人民币资产")
        default: L("{c} 资产", ["c": code])
        }
    }
}

private struct AccountRow: View {
    let line: BalanceLine
    let date: String
    let observedOn: String?
    let commodity: String?
    let statement: Set<String>?
    @State private var editing = false

    var body: some View {
        let hasStatement = statement?.contains(line.account) ?? false
        let canValue = line.account.hasPrefix("Assets:") && !hasStatement
        let original = NativeBalances.total([line])
        let hasBalance = observedOn != nil || !original.isEmpty || line.balance.value != 0
        let amounts = original.isEmpty && hasBalance && line.balance.value == 0
            ? commodity.map { [$0: Decimal.zero] } ?? [:]
            : original
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 16) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(accountTitle).font(.ui(13.5)).foregroundStyle(Theme.ink)
                    if let note = line.note, !note.isEmpty {
                        Text(note).font(.ui(11)).foregroundStyle(Theme.muted)
                    }
                    if let observedOn {
                        Text(L("余额记录于 {d}", ["d": Format.day(observedOn)]))
                            .font(.ui(11)).foregroundStyle(Theme.muted)
                    } else if !hasBalance {
                        Text(L("暂无余额记录")).font(.ui(11)).foregroundStyle(Theme.muted)
                    }
                }
                Spacer()
                NativeBalances(amounts: amounts)
                    .font(.mono(13.5)).foregroundStyle(Theme.ink)
                // The action slot is always reserved, so amounts never jump sideways when it appears.
                Group {
                    if commodity != nil, statement != nil, hasStatement || canValue {
                        LinkButton(title: hasStatement ? L("核对余额") : L("更新估值"), arrow: false) { editing.toggle() }
                    } else {
                        Color.clear
                    }
                }
                .frame(width: 72, alignment: .trailing)
            }
            .padding(.vertical, 16)
            if editing, let commodity {
                AccountEditor(account: line.account, commodity: commodity, date: date, statement: hasStatement)
                    .id("\(line.account)|\(date)|\(commodity)")
                    .padding(.bottom, 16)
            }
            Hairline(color: Theme.border.opacity(0.6))
        }
    }

    private var accountTitle: String {
        if line.assetClass == "term_deposit", line.account.hasSuffix(":ReportedTotal"), let bank = line.institution {
            return Format.segment(bank)
        }
        return Format.account(line.account)
    }
}

struct NativeBalances: View {
    let amounts: [String: Decimal]

    static func total(_ lines: [BalanceLine]) -> [String: Decimal] {
        var totals: [String: Decimal] = [:]
        for line in lines {
            for (code, amount) in line.originalBalances ?? [:] {
                totals[code, default: 0] += amount.value
            }
        }
        return totals
    }

    var body: some View {
        if amounts.isEmpty {
            Text("—")
        } else {
            let codes = amounts.keys.sorted { ($0 == "USD" ? 0 : 1, $0) < ($1 == "USD" ? 0 : 1, $1) }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { values(codes) }.fixedSize()
                VStack(alignment: .trailing, spacing: 3) { values(codes) }.fixedSize()
            }
        }
    }

    private func values(_ codes: [String]) -> some View {
        ForEach(codes, id: \.self) { code in
            let amount = amounts[code, default: 0]
            let fraction = NSDecimalNumber(decimal: amount).stringValue.split(separator: ".").dropFirst().first
            let formatted = Format.money(amount, currency: code, digits: max(2, fraction?.count ?? 0))
            Text(formatted).accessibilityLabel(code + " " + formatted)
        }
    }
}

struct ReportCurrencyPicker: View {
    @Binding var currency: String

    var body: some View {
        Picker(L("报表显示币种"), selection: $currency) {
            Text("USD").tag("USD")
            Text("CNY").tag("CNY")
        }.pickerStyle(.segmented).labelsHidden().fixedSize()
            .accessibilityLabel(L("报表显示币种"))
    }
}

struct AssetMissingRates: View {
    let pairs: [String]

    var body: some View {
        ErrorLine(message: L("缺少汇率（{pairs}），暂无法折算净资产。", ["pairs": pairs.joined(separator: ", ")]))
    }
}

/// Check a statement balance against the ledger, or record what an asset is worth on a date.
struct AccountEditor: View {
    @Environment(AppModel.self) private var model
    let account: String?  // nil: a new asset, named here
    let commodity: String
    let date: String
    let statement: Bool
    @State private var name = "Assets:"
    @State private var amount = ""
    @State private var day = ""
    @State private var saving = false
    @State private var result: [Discrepancy]?
    @State private var saved = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text((statement ? L("填账单同一天的余额，信用卡欠款用负数。") : L("填这项资产当时的总价值，估值变动会自动计入净资产。"))
                + " " + L("币种：{c}", ["c": commodity]))
                .font(.ui(11.5)).foregroundStyle(Theme.muted)
            HStack(alignment: .bottom, spacing: 12) {
                if account == nil {
                    labeled(L("账户")) { KeelField(placeholder: "Assets:Property:Home", text: $name, width: 260, mono: true) }
                }
                labeled(statement ? L("账单上的当前余额") : L("市值")) { KeelField(placeholder: "0.00", text: $amount, width: 170, mono: true) }
                labeled(statement ? L("余额日期") : L("日期")) { KeelField(placeholder: "YYYY-MM-DD", text: $day, width: 150, mono: true) }
                Button(saving ? L("保存中…") : statement ? L("核对余额") : L("保存估值")) { Task { await save() } }
                    .buttonStyle(.keelPrimary)
                    .disabled(saving || amount.isEmpty || day.isEmpty)
            }
            if let error { ErrorLine(message: error) }
            if let result {
                if result.isEmpty {
                    Text("✓ " + L("余额一致")).font(.ui(13)).foregroundStyle(Theme.inflow)
                } else {
                    ForEach(result) { d in
                        Text(L("账单 {a} · 账本 {b} · 差额 {c}", [
                            "a": Format.money(d.statement, currency: d.commodity, digits: 2),
                            "b": Format.money(d.ledger, currency: d.commodity, digits: 2),
                            "c": Format.money(d.diff, currency: d.commodity, digits: 2),
                        ]))
                        .font(.ui(13)).foregroundStyle(Theme.outflow)
                    }
                }
            }
            if saved { Text("✓ " + L("已记录")).font(.ui(13)).foregroundStyle(Theme.inflow) }
        }
        .padding(16)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(Theme.border))
        .padding(.top, 12)
        .onAppear { day = date }
    }

    private func labeled<C: View>(_ label: String, @ViewBuilder _ content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.ui(11.5)).foregroundStyle(Theme.muted)
            content()
        }
    }

    private func save() async {
        guard let api = model.api else { return }
        saving = true
        defer { saving = false }
        error = nil
        result = nil
        saved = false
        let target = account ?? name.trimmingCharacters(in: .whitespaces)
        do {
            if statement {
                result = try await api.checkBalance(BalanceIn(account: target, date: day, amount: amount, commodity: commodity)).discrepancies
                model.banner = L("余额已核对")
            } else {
                _ = try await api.recordValuation(ValuationIn(account: target, date: day, value: amount, commodity: commodity))
                saved = true
                model.banner = L("已记录市值")
            }
            model.month = String(day.prefix(7))
            model.revision += 1
        } catch {
            self.error = error.localizedDescription
        }
    }
}
