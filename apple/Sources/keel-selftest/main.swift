import Foundation
import KeelCore

// Usage: keel-selftest <path to keel-engine> <PF_APP_HOME with a ledger>
// Starts the real engine the way the app does, waits for KEEL_READY, then fetches and decodes
// every response the native screens use, in both languages. Exit 1 on the first failure.

@MainActor
func run() async -> Int32 {
    let args = CommandLine.arguments
    guard args.count >= 3 else {
        print("usage: keel-selftest <keel-engine> <app-home>")
        return 2
    }
    let engine = Engine(
        executable: URL(fileURLWithPath: args[1]),
        environment: ["PF_APP_HOME": args[2], "PF_NOTIFY": "0", "PF_LOG_DIR": args[2] + "/logs"]
    )
    engine.start()
    let deadline = Date().addingTimeInterval(120)
    while engine.baseURL == nil {
        if case let .failed(message) = engine.state {
            print("✗ engine failed: \(message)")
            return 1
        }
        if Date() > deadline {
            print("✗ engine not ready after 120 s")
            return 1
        }
        try? await Task.sleep(for: .milliseconds(200))
    }
    let base = engine.baseURL!
    print("✓ engine ready at \(base)")
    defer { engine.stop() }

    func check(_ name: String, _ body: () async throws -> String) async -> Bool {
        do {
            print("✓ \(name): \(try await body())")
            return true
        } catch {
            print("✗ \(name): \(error)")
            return false
        }
    }

    var ok = true
    for lang in ["zh", "en"] {
        let api = API(base: base, language: lang)
        ok = await check("[\(lang)] home") {
            let h = try await api.home()
            return "has_data=\(h.hasData) todos=\(h.todos.count) \(h.todos.first?.title ?? "")"
        } && ok
        ok = await check("[\(lang)] China / nonresident / statement codes") {
            var china = EmploymentComparisonSettings()
            china.chinaGrossIncome = 300_000
            china.socialInsuranceAnnual = 18_000
            china.housingFundAnnual = 12_000
            let cn = try await api.chinaEmploymentTax(china)
            guard cn.currency == "CNY", abs(cn.netIncome + cn.incomeTax + cn.employeeContributions - cn.grossIncome) < 0.01 else {
                throw APIError(status: 0, message: "China tax reconciliation failed")
            }
            var nr = EmploymentComparisonSettings()
            nr.grossIncome = 100_000; nr.ficaExempt = true; nr.treatyExemptIncome = 5_000
            let tax = try await api.nonresidentEmploymentTax(nr, residence: "nj")
            guard tax.socialSecurity == 0, tax.medicare == 0 else { throw APIError(status: 0, message: "FICA selection failed") }
            let codes = try await api.statementPasswords()
            return "CNY reconciled, NRA decoded, code providers=\(codes.count)"
        } && ok
        ok = await check("[\(lang)] employment tax") {
            var input = EmploymentComparisonSettings()
            input.grossIncome = 200_000
            input.retirementContribution = 20_000
            let tax = try await api.employmentTax(EmploymentTaxInput(input, residence: "nj"))
            guard tax.taxYear == 2026, tax.nycTax == 0, tax.njCredit <= tax.njTaxBeforeCredit,
                  abs(tax.netIncome + tax.totalTax + tax.retirementContribution - tax.grossIncome) < 0.01 else {
                throw APIError(status: 0, message: "employment tax reconciliation failed")
            }
            return "year=\(tax.taxYear), sources=\(tax.sources.count), reconciled"
        } && ok
        ok = await check("[\(lang)] pulse") {
            let p = try await api.pulse()
            return "as_of=\(p.asOf ?? "-") spent=\(p.spent) forecast=\(p.forecast) upcoming=\(p.upcoming.count) alerts=\(p.alerts.map(\.title))"
        } && ok
        ok = await check("[\(lang)] overview") {
            let h = try await api.home()
            guard let month = h.sentence?.month else { return "no month (empty ledger)" }
            let o = try await api.overview(month: month)
            return "\(o.month) top=\(o.topExpenses.count) series=\(o.series.count) insights=\(o.insights.count)"
        } && ok
    }
    let api = API(base: base, language: "zh")
    ok = await check("transactions") {
        let page = try await api.transactions(status: "pending", limit: 20)
        return "pending total=\(page.total) first=\(page.items.first?.description ?? "-")"
    } && ok
    ok = await check("cashline") {
        let p = try await api.pulse()
        guard let asOf = p.asOf else { return "no data" }
        let days = try await api.cashline(start: String(asOf.prefix(8)) + "01", end: asOf)
        guard days.last?.balance == p.cashNow else {
            throw APIError(status: 0, message: "cash \(days.last?.balance.raw ?? "-") != \(p.cashNow.raw)")
        }
        return "\(days.count) days, cash \(p.cashNow)"
    } && ok
    ok = await check("owner names") { "\(try await api.ownerNames().map(\.name))" } && ok
    ok = await check("categories") { "\(try await api.categories().count) categories" } && ok
    ok = await check("income breakdown") {
        let p = try await api.pulse()
        guard let month = p.month else { return "empty ledger" }
        let income = try await api.income(month: month)
        let h = try await api.home(month: month)
        let total = income.filter { $0.depth == 2 }.reduce(Decimal.zero) { $0 + $1.amount.value }
        guard total == h.sentence?.income.value else {
            throw APIError(status: 0, message: "income breakdown disagrees with monthly income")
        }
        return "\(income.count) categories, total \(total)"
    } && ok
    ok = await check("plan defaults") {
        let d = try await api.simDefaults()
        return "facts=\(d.facts != nil), initial=\(d.scenario.initial)"
    } && ok
    ok = await check("retirement account projection") {
        let d = try await api.simDefaults()
        var state = PlanState(scenario: d.scenario, model: d.model)
        state.scenario.retirementBalance = state.scenario.initial / 2
        state.scenario.annualRetirementContribution = 10_000
        state.scenario.retirementAccessYear = 25
        state.scenario.retirementWithdrawalTaxRate = 0.2
        let r = try await api.simulate(state, paths: 100, horizon: 5)
        guard r.terminalMedian.isFinite else { throw APIError(status: 0, message: "Non-finite retirement projection") }
        return "decoded \(r.paths) paths, \(r.years.count - 1) years"
    } && ok
    ok = await check("settings") {
        var p = try await api.prefs()
        p.lowBalance = "750"
        let saved = try await api.savePrefs(p)
        return "language=\(saved.language) low_balance=\(saved.lowBalance)"
    } && ok
    ok = await check("version") {
        let v = try await api.version()
        return "commit=\(v.commit ?? "-") branch=\(v.branch ?? "-") source_mode=\(v.sourceMode)"
    } && ok
    ok = await check("format") {
        let s = Format.money(Decimal(string: "-1234.6")!) + " " + Format.month("2026-09", lang: .en)
        guard s == "−$1,235 Sep 2026" else { throw APIError(status: 0, message: "got \(s)") }
        return s
    } && ok
    print(ok ? "SELFTEST OK" : "SELFTEST FAILED")
    return ok ? 0 : 1
}

let status = await run()
exit(status)
