import Charts
import KeelCore
import SwiftUI

/// 买房 / 租房 / 贷款: four calculators, prefilled where the ledger clearly knows the number (a
/// property's valuation, a mortgage balance, the rent paid last month).
struct HousingView: View {
    @Environment(AppModel.self) private var model
    @State private var tab = "mortgage"
    @State private var prefill: Prefill?

    struct Prefill {
        var price: Double?
        var rent: Double?
        var mortgage: Double?
        var notes: [String: String] = [:]  // price | rent | mortgage → where it came from
    }

    var body: some View {
        Page {
            VStack(alignment: .leading, spacing: 28) {
                Segmented(
                    items: [("mortgage", L("房贷")), ("cash", L("全款还是贷款")), ("rentbuy", L("租还是买")), ("rental", L("出租房回报"))],
                    selection: $tab
                )
                if let p = prefill {
                    // All four stay alive: switching is instant and each keeps its inputs.
                    ZStack(alignment: .topLeading) {
                        Calculator(spec: .mortgage(p)).opacity(tab == "mortgage" ? 1 : 0).allowsHitTesting(tab == "mortgage")
                        Calculator(spec: .cashVsLoan(p)).opacity(tab == "cash" ? 1 : 0).allowsHitTesting(tab == "cash")
                        Calculator(spec: .rentVsBuy(p)).opacity(tab == "rentbuy" ? 1 : 0).allowsHitTesting(tab == "rentbuy")
                        Calculator(spec: .rental(p)).opacity(tab == "rental" ? 1 : 0).allowsHitTesting(tab == "rental")
                    }
                    Text(L("按输入的房价和利率估算，金额未扣除通胀；税务优惠、资本利得和折旧因人而异，未计入结果。"))
                        .font(.ui(11.5)).foregroundStyle(Theme.muted)
                } else {
                    Text(L("正在读账本里的住房信息…")).font(.ui(13)).foregroundStyle(Theme.muted)
                }
            }
        }
        .task { prefill = await load() }
    }

    private func load() async -> Prefill {
        var p = Prefill()
        guard let api = model.api else { return p }
        let range = try? await api.range()
        let month = range?.last.map { AppModel.shift($0, -1) }
        async let bsTask = try? api.balanceSheet()
        async let expTask: [ExpenseNode]? = month == nil ? nil : try? api.expenses(month: month!)
        let bs = await bsTask
        let exp = await expTask
        if let property = bs?.lines.filter({ $0.type == "Assets" && $0.assetClass == "real_estate" && $0.balance.double > 0 })
            .max(by: { $0.balance.double < $1.balance.double }) {
            p.price = property.balance.double
            p.notes["price"] = L("房价来自 {a} 的 {d} 估值", ["a": Format.account(property.account), "d": bs!.date])
        }
        let isMortgage = { (name: String) in
            ["mortgage", "房贷", "按揭"].contains { name.localizedCaseInsensitiveContains($0) }  // l10n-allow: matches account names
        }
        if let loan = bs?.lines.filter({ $0.type == "Liabilities" && isMortgage($0.account) && $0.balance.double < 0 })
            .min(by: { $0.balance.double < $1.balance.double }) {
            p.mortgage = abs(loan.balance.double)
            p.notes["mortgage"] = L("贷款金额来自 {a} 的 {d} 余额", ["a": Format.account(loan.account), "d": bs!.date])
        }
        if let rent = exp?.first(where: { $0.category == "Expenses:Housing:Rent" && $0.amount.double > 0 }) {
            p.rent = rent.amount.double
            p.notes["rent"] = L("月租来自 {m} 的房租支出", ["m": month ?? ""])
        }
        return p
    }
}

// MARK: - calculators

enum FieldKind { case money, pct, years }

struct CalcField {
    let key: String
    let label: String
    let kind: FieldKind
}

/// One calculator: inputs (three up front, the rest under 更多假设) on the left, the answer, KPIs
/// and a chart on the right. Inputs are posted to /api/calc/<tool> 250 ms after the last edit.
enum CalcSpec {
    case mortgage(HousingView.Prefill)
    case cashVsLoan(HousingView.Prefill)
    case rentVsBuy(HousingView.Prefill)
    case rental(HousingView.Prefill)

    var tool: String {
        switch self {
        case .mortgage: return "mortgage"
        case .cashVsLoan: return "cash-vs-loan"
        case .rentVsBuy: return "rent-vs-buy"
        case .rental: return "rental"
        }
    }

    var initial: [String: Double] {
        switch self {
        case let .mortgage(p): return ["principal": p.mortgage ?? 400_000, "annual_rate": 0.065, "years": 30, "extra_monthly": 0]
        case let .cashVsLoan(p):
            return ["loan": p.mortgage ?? 400_000, "annual_rate": 0.065, "years": 30, "invest_return": 0.07, "horizon_years": 30]
        case let .rentVsBuy(p):
            return [
                "price": p.price ?? 800_000, "rent": p.rent ?? 3500, "down_pct": 0.2, "annual_rate": 0.065, "years": 30,
                "closing_buy_pct": 0.03, "selling_cost_pct": 0.06, "property_tax_pct": 0.012, "insurance": 1800,
                "maintenance_pct": 0.01, "hoa": 0, "appreciation": 0.03, "rent_growth": 0.03, "renters_insurance": 240,
                "invest_return": 0.07, "horizon_years": 30,
            ]
        case let .rental(p):
            return [
                "price": p.price ?? 600_000, "rent": 3200, "down_pct": 0.25, "annual_rate": 0.07, "years": 30, "closing_buy_pct": 0.03,
                "vacancy_pct": 0.05, "management_pct": 0.08, "property_tax_pct": 0.012, "insurance": 2000, "maintenance_pct": 0.01,
                "capex_reserve_pct": 0.005, "hoa": 0, "tenant_turnover_cost": 1500, "appreciation": 0.03, "rent_growth": 0.03,
                "expense_growth": 0.03, "selling_cost_pct": 0.06, "hold_years": 10, "tbill_rate": 0.041,
            ]
        }
    }

    var fields: [CalcField] {
        switch self {
        case .mortgage:
            return [
                .init(key: "principal", label: L("贷款金额"), kind: .money), .init(key: "annual_rate", label: L("年利率"), kind: .pct),
                .init(key: "years", label: L("年限"), kind: .years), .init(key: "extra_monthly", label: L("每月额外还本"), kind: .money),
            ]
        case .cashVsLoan:
            return [
                .init(key: "loan", label: L("可贷款金额（否则付现）"), kind: .money), .init(key: "annual_rate", label: L("房贷利率"), kind: .pct),
                .init(key: "years", label: L("贷款年限"), kind: .years), .init(key: "invest_return", label: L("这笔钱拿去投资的年收益"), kind: .pct),
                .init(key: "horizon_years", label: L("比较多少年"), kind: .years),
            ]
        case .rentVsBuy:
            return [
                .init(key: "price", label: L("房价"), kind: .money), .init(key: "rent", label: L("同等房子月租"), kind: .money),
                .init(key: "down_pct", label: L("首付比例"), kind: .pct), .init(key: "annual_rate", label: L("房贷利率"), kind: .pct),
                .init(key: "property_tax_pct", label: L("房产税（占房价/年）"), kind: .pct), .init(key: "maintenance_pct", label: L("维修（占房价/年）"), kind: .pct),
                .init(key: "insurance", label: L("房屋保险/年"), kind: .money), .init(key: "hoa", label: L("物业费/月"), kind: .money),
                .init(key: "closing_buy_pct", label: L("买房交易成本"), kind: .pct), .init(key: "selling_cost_pct", label: L("卖房交易成本"), kind: .pct),
                .init(key: "appreciation", label: L("房价年涨幅"), kind: .pct), .init(key: "rent_growth", label: L("房租年涨幅"), kind: .pct),
                .init(key: "invest_return", label: L("投资年收益"), kind: .pct), .init(key: "horizon_years", label: L("比较年数"), kind: .years),
                .init(key: "years", label: L("贷款年限"), kind: .years), .init(key: "renters_insurance", label: L("租客保险/年"), kind: .money),
            ]
        case .rental:
            return [
                .init(key: "price", label: L("房价"), kind: .money), .init(key: "rent", label: L("月租金"), kind: .money),
                .init(key: "down_pct", label: L("首付比例"), kind: .pct), .init(key: "annual_rate", label: L("贷款利率"), kind: .pct),
                .init(key: "vacancy_pct", label: L("空置率"), kind: .pct), .init(key: "management_pct", label: L("管理费（占租金）"), kind: .pct),
                .init(key: "property_tax_pct", label: L("房产税（占房价/年）"), kind: .pct), .init(key: "insurance", label: L("保险/年"), kind: .money),
                .init(key: "maintenance_pct", label: L("维修（占房价/年）"), kind: .pct), .init(key: "capex_reserve_pct", label: L("大修储备（占房价/年）"), kind: .pct),
                .init(key: "hoa", label: L("物业费/月"), kind: .money), .init(key: "tenant_turnover_cost", label: L("找租客成本/年"), kind: .money),
                .init(key: "appreciation", label: L("房价年涨幅"), kind: .pct), .init(key: "rent_growth", label: L("租金年涨幅"), kind: .pct),
                .init(key: "selling_cost_pct", label: L("卖房成本"), kind: .pct), .init(key: "hold_years", label: L("持有"), kind: .years),
                .init(key: "tbill_rate", label: L("机会成本（短期国债收益率）"), kind: .pct),
                .init(key: "years", label: L("贷款年限"), kind: .years), .init(key: "expense_growth", label: L("费用年涨幅"), kind: .pct),
            ]
        }
    }

    var prefill: HousingView.Prefill {
        switch self {
        case let .mortgage(p), let .cashVsLoan(p), let .rentVsBuy(p), let .rental(p): return p
        }
    }

    var source: String {
        let keys: [String]
        switch self {
        case .mortgage, .cashVsLoan: keys = ["mortgage"]
        case .rentVsBuy: keys = ["price", "rent"]
        case .rental: keys = ["price"]
        }
        let notes = keys.compactMap { prefill.notes[$0] }
        return notes.isEmpty
            ? L("金额、利率和年限先用示例假设，请按自己的情况修改。")
            : notes.joined(separator: L("；")) + L("。其余金额、利率和年限先用示例假设，可随时修改。")
    }
}

private struct Calculator: View {
    @Environment(AppModel.self) private var model
    let spec: CalcSpec
    @State private var values: [String: Double]
    @State private var more = false
    @State private var mortgage: MortgageResult?
    @State private var cash: CashVsLoanResult?
    @State private var rentBuy: RentVsBuyResult?
    @State private var rental: RentalResult?
    @State private var failed = false

    init(spec: CalcSpec) {
        self.spec = spec
        _values = State(initialValue: spec.initial)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ToolHeading(question: question, answer: failed ? L("调整一下假设，我们再算一次。") : answer, answerColor: answerColor)
            HStack(alignment: .top, spacing: 40) {
                form.frame(width: 340)
                VStack(alignment: .leading, spacing: 0) { if !failed { result } }
                    .frame(maxWidth: .infinity, alignment: .topLeading)
            }
        }
        .task(id: values) {
            guard !values.isEmpty, let api = model.api else { return }
            try? await Task.sleep(for: .milliseconds(250))
            if Task.isCancelled { return }
            do {
                switch spec {
                case .mortgage: mortgage = try await api.calc(spec.tool, values)
                case .cashVsLoan: cash = try await api.calc(spec.tool, values)
                case .rentVsBuy: rentBuy = try await api.calc(spec.tool, values)
                case .rental: rental = try await api.calc(spec.tool, values)
                }
                failed = false
            } catch is CancellationError {
            } catch {
                failed = true
            }
        }
    }

    private var form: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(spec.source).font(.ui(11.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            ForEach(spec.fields.prefix(3), id: \.key) { field($0) }
            if spec.fields.count > 3 {
                DisclosureRow(title: L("更多假设"), open: $more)
                if more {
                    LazyVGrid(columns: [GridItem(.flexible(), spacing: 14), GridItem(.flexible())], alignment: .leading, spacing: 14) {
                        ForEach(spec.fields.dropFirst(3), id: \.key) { field($0) }
                    }
                }
            }
            if failed { ErrorLine(message: L("暂时算不出来，请检查输入的金额、利率和年限。")) }
        }
    }

    private func field(_ f: CalcField) -> some View {
        let raw = values[f.key] ?? 0
        let suffix = f.kind == .pct ? " %" : f.kind == .years ? L("（年）") : ""
        return Field(label: f.label + suffix) {
            NumberField(value: f.kind == .pct ? (raw * 100 * 10000).rounded() / 10000 : raw) { v in
                values[f.key] = f.kind == .pct ? v / 100 : v
            }
        }
    }

    // MARK: per calculator

    private var question: String {
        switch spec {
        case .mortgage: return L("买房还是租房？全款还是贷款？")
        case .cashVsLoan: return L("全款还是贷款？")
        case .rentVsBuy: return L("买房还是租房？")
        case .rental: return L("把房子租出去划算吗？")
        }
    }

    private var answer: String {
        switch spec {
        case .mortgage:
            guard let r = mortgage else { return L("正在算每个月要还多少。") }
            return L("借 {p}，每月还 {m}，{y} 年还清。", [
                "p": m0(values["principal"] ?? 0), "m": Format.money(Decimal(r.payment), digits: 2), "y": String(format: "%.1f", Double(r.months) / 12),
            ])
        case .cashVsLoan:
            guard let r = cash else { return L("正在比较全款和贷款。") }
            return r.loanBetterAtHorizon ? L("贷款 + 把现金拿去投资更划算") : L("全款更划算")
        case .rentVsBuy:
            guard let r = rentBuy else { return L("正在比较买房和租房。") }
            let n = Int(values["horizon_years"] ?? 30)
            guard let y = r.breakevenYear else { return L("{n} 年内买房都不如租房", ["n": n]) }
            return y == 0 ? L("买房从一开始就更划算") : L("住满 {n} 年后买房开始划算", ["n": y])
        case .rental:
            guard let r = rental else { return L("正在算这套房子带来的收入。") }
            let tbill = pct1(values["tbill_rate"] ?? 0)
            return r.worthIt ? L("值得：持有期年化回报 {a} 高于短期国债 {b}", ["a": pct1(r.irr), "b": tbill]) : L("不如买短期国债：年化回报 {a} ≤ {b}", ["a": pct1(r.irr), "b": tbill])
        }
    }

    private var answerColor: Color {
        if case .rental = spec, let r = rental { return r.worthIt ? Theme.inflow : Theme.outflow }
        return Theme.ink
    }

    @ViewBuilder private var result: some View {
        switch spec {
        case .mortgage:
            if let r = mortgage {
                Tiles {
                    StatTile(label: L("月供"), value: Format.money(Decimal(r.payment), digits: 2))
                    StatTile(label: L("总利息"), value: m0(r.totalInterest))
                    StatTile(label: L("还清用时"), value: L("{n} 年", ["n": String(format: "%.1f", Double(r.months) / 12)]))
                    StatTile(label: L("额外还本省下利息"), value: m0(r.interestSaved))
                }
                Text(L("每年还的利息与本金")).font(.display(18)).foregroundStyle(Theme.ink).padding(.top, 28)
                BarExhibitChart(
                    xLabels: (1...max(1, r.interestByYear.count)).map { L("第 {n} 年", ["n": $0]) },
                    series: [
                        ExhibitSeries(label: L("利息"), values: r.interestByYear, color: Theme.signal),
                        ExhibitSeries(label: L("本金"), values: r.principalByYear, color: Theme.line2, strong: false),
                    ]
                )
                .padding(.top, 8)
            }
        case .cashVsLoan:
            if let r = cash {
                let n = Int(values["horizon_years"] ?? 30)
                Tiles {
                    StatTile(label: L("第 {n} 年净资产差（贷款 − 全款）", ["n": n]), value: m0(r.advantageByYear.last ?? 0))
                    StatTile(label: L("打平所需投资收益"), value: pct1(r.breakevenReturn), sub: L("房贷利率 {p}", ["p": pct1(values["annual_rate"] ?? 0)]))
                    StatTile(label: L("月供"), value: Format.money(Decimal(r.payment), digits: 2), sub: L("全款方案把它按月投资"))
                }
                LineExhibitChart(
                    xLabels: r.advantageByYear.indices.map { L("第 {n} 年", ["n": $0]) },
                    series: [ExhibitSeries(label: L("贷款 − 全款"), values: r.advantageByYear)],
                    includeZero: true,
                    height: 240
                )
                .padding(.top, 28)
                Text(L("两种方案每月从口袋里拿出的钱一样多：全款方案把省下的月供按月投资。")).font(.ui(11.5)).foregroundStyle(Theme.muted).padding(.top, 8)
            }
        case .rentVsBuy:
            if let r = rentBuy {
                let n = Int(values["horizon_years"] ?? 30)
                Tiles {
                    StatTile(label: L("买房首年月成本"), value: m0(r.monthlyCostBuyYear1), sub: L("月供+税+险+维修+物业"))
                    StatTile(label: L("租房首年月成本"), value: m0(r.monthlyCostRentYear1))
                    StatTile(label: L("买房一次性付出"), value: m0(r.upfrontCash), sub: L("首付+交易成本"))
                    StatTile(label: L("第 {n} 年 买 − 租", ["n": n]), value: m0((r.buyNetWorth.last ?? 0) - (r.rentNetWorth.last ?? 0)))
                }
                LineExhibitChart(
                    xLabels: r.buyNetWorth.indices.map { L("第 {n} 年", ["n": $0]) },
                    series: [
                        ExhibitSeries(label: L("买房"), values: r.buyNetWorth),
                        ExhibitSeries(label: L("租房"), values: r.rentNetWorth, color: Theme.series[1], strong: false),
                    ],
                    height: 260
                )
                .padding(.top, 28)
                Text(L("买房 = 卖掉后的净值 + 投资；租房 = 把差额拿去投资。")).font(.ui(11.5)).foregroundStyle(Theme.muted).padding(.top, 8)
            }
        case .rental:
            if let r = rental {
                Tiles {
                    StatTile(label: L("首年每月现金流"), value: m0(r.monthlyCashFlowYear1), sub: L("扣除所有费用和月供"))
                    StatTile(label: L("资本化率"), value: pct1(r.capRate), sub: L("净运营收入 / 房价"))
                    StatTile(label: L("现金回报率"), value: pct1(r.cashOnCash), sub: L("投入现金 {x}", ["x": m0(r.cashInvested)]))
                    StatTile(label: L("盈亏平衡租金"), value: m0(r.breakevenRent), sub: L("当前 {x}", ["x": m0(values["rent"] ?? 0)]))
                }
                Tiles {
                    StatTile(label: L("首年净运营收入"), value: m0(r.noiYear1))
                    StatTile(label: L("首年还贷"), value: m0(r.debtServiceYear1))
                    StatTile(label: L("卖房到手"), value: m0(r.saleProceeds), sub: L("第 {n} 年", ["n": Int(values["hold_years"] ?? 10)]))
                    StatTile(
                        label: L("同样的钱买短期国债"), value: m0(r.tbillEquivalent),
                        sub: L("回报倍数 {a}× vs {b}×", ["a": String(format: "%.2f", r.equityMultiple), "b": String(format: "%.2f", r.tbillEquivalent / max(1, r.cashInvested))])
                    )
                }
                .padding(.top, 24)
                Text(L("每年现金流")).font(.display(18)).foregroundStyle(Theme.ink).padding(.top, 28).padding(.bottom, 8)
                BarExhibitChart(
                    xLabels: (1...max(1, r.cashFlowsByYear.count)).map { L("第 {n} 年", ["n": $0]) },
                    series: [ExhibitSeries(label: L("现金流"), values: r.cashFlowsByYear)],
                    bySign: true,
                    height: 220
                )
            }
        }
    }
}

/// KPI tiles in a row separated by hairlines.
private struct Tiles<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        HStack(alignment: .top, spacing: 20) { content }
            .fixedSize(horizontal: false, vertical: true)
    }
}
