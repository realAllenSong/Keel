import AppKit
import KeelCore
import SwiftUI

/// Compare two residences for the same NYC job, without replacing a saved plan until applied.
struct EmploymentComparisonSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let store: PlanStore
    @State private var input: EmploymentComparisonSettings
    @State private var nyc: EmploymentTaxResult?
    @State private var nj: EmploymentTaxResult?
    @State private var china: ChinaTaxResult?
    @State private var computedKey: String?
    @State private var error: String?
    @State private var details = false
    @State private var invalidFields: Set<String> = []

    init(store: PlanStore, state: PlanState) {
        self.store = store
        let historical = store.defaults?.planningBasis?.spending ?? []
        func amount(_ root: String) -> Double {
            historical.filter { $0.category == root || $0.category.hasPrefix(root + ":") }.reduce(0) { $0 + $1.annualAmount }
        }
        let initial = state.employmentComparison ?? EmploymentComparisonSettings.prefill(
            annualSpending: state.scenario.annualSpending,
            historicalHousing: amount("Expenses:Housing"), historicalTransport: amount("Expenses:Transport"))
        _input = State(initialValue: initial)
    }

    private var context: String { input.taxContext ?? "us_resident" }
    private var taxKey: String { "\(context)|\(input.grossIncome)|\(input.retirementContribution)|\(input.filingStatus)|\(input.ficaExempt ?? false)|\(input.treatyExemptIncome ?? 0)|\(input.itemizedDeductions ?? 0)|\(input.chinaGrossIncome ?? 0)|\(input.socialInsuranceAnnual ?? 0)|\(input.housingFundAnnual ?? 0)|\(input.specialDeductionsAnnual ?? 0)|\(input.otherDeductionsAnnual ?? 0)" }
    private var chinaReady: Bool { invalidFields.isEmpty && computedKey == taxKey && china != nil && (input.chinaGrossIncome ?? 0) > 0 && (input.cnyPerUSD ?? 0) > 0 }

    private var ready: Bool { context != "china_resident" && invalidFields.isEmpty && computedKey == taxKey && nyc != nil && nj != nil && input.grossIncome > 0 && (nyc?.netIncome ?? -1) >= 0 && (nj?.netIncome ?? -1) >= 0 }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text(context == "china_resident" ? L("中国工作 · 税后现金流") : L("住纽约市还是住新泽西 · 同一份纽约工作")).font(.display(24))
                    Text(context == "china_resident" ? L("2026 税年 · 全年中国税收居民 · 工资薪金综合所得") : context == "us_nonresident" ? L("2026 税年 · 美国非税务居民 · 单份 W-2 纽约工作") : L("2026 税年 · 全年美国税务居民 · 单份 W-2 工资 · 标准扣除")).font(.ui(12)).foregroundStyle(Theme.muted)
                }
                Spacer()
                Button(L("关闭")) { dismiss() }.buttonStyle(.keelOutlineCompact).keyboardShortcut(.cancelAction)
            }.padding(24)
            Hairline()
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Picker(L("税务身份与工作场景"), selection: Binding(get: { context }, set: { input.taxContext = $0; input.filingStatus = "single"; invalidFields.removeAll(); error = nil; computedKey = nil })) {
                        Text(L("美国税务居民")).tag("us_resident")
                        Text(L("美国非税务居民")).tag("us_nonresident")
                        Text(L("中国税收居民")).tag("china_resident")
                    }.pickerStyle(.segmented)
                    if context == "china_resident" { chinaForm } else {
                    Text(context == "us_nonresident" ? L("按 1040-NR 常规工资所得估算，不自动使用标准扣除。税务居民身份不等于签证身份；须自行确认全年非居民资格。") : L("比较的是年度税负估算，不是工资单预扣。夫妻合报限一人有工资、无其他收入、无受抚养人；按未满 65 岁、非盲人、非退伍军人计算。"))
                        .font(.ui(12)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                    HStack(alignment: .top, spacing: 20) {
                        inputField(L("税前年工资与奖金"), value: $input.grossIncome)
                        inputField(L("传统 401(k) 年缴款"), value: $input.retirementContribution)
                        VStack(alignment: .leading, spacing: 6) {
                            Text(L("报税身份")).font(.ui(12)).foregroundStyle(Theme.muted)
                            Picker(L("报税身份"), selection: $input.filingStatus) {
                                Text(L("单身")).tag("single")
                                if context == "us_nonresident" { Text(L("已婚分别申报")).tag("married_filing_separately") } else { Text(L("夫妻合报 · 一人工资")).tag("married_filing_jointly") }
                            }.labelsHidden().frame(width: 210)
                        }
                    }
                    if context == "us_nonresident" {
                        Toggle(L("符合 F / J 等身份的 FICA 豁免条件"), isOn: Binding(get: { input.ficaExempt ?? false }, set: { input.ficaExempt = $0 })).toggleStyle(.checkbox).font(.ui(12))
                        HStack(spacing: 20) {
                            inputField(L("已确认符合协定的工资免税额"), value: optional(\.treatyExemptIncome))
                            inputField(L("合格 Schedule A 逐项扣除总额"), value: optional(\.itemizedDeductions))
                        }
                        Text(L("FICA 豁免和税收协定资格需分别确认，不会因持 F-1 自动认定。中国学生的 $5,000 条款仅在满足协定条件时适用；逐项扣除填已按上限核实的金额。可能触发 AMT 的输入会提示另行核算。")).font(.ui(11.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                    }
                    inputField(L("其他年开销（不含下面的住房与交通）"), value: $input.otherAnnualSpending)
                    Text(L("预填用于拆分当前年开销；住房和交通参考历史金额，请按两个居住地分别核对。")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                    HStack(alignment: .top, spacing: 18) {
                        residenceCard(L("住纽约市"), residence: "nyc", result: nyc, housing: $input.nycHousingMonthly, commute: $input.nycCommuteMonthly)
                        residenceCard(L("新泽西居民 · 纽约工作"), residence: "nj", result: nj, housing: $input.njHousingMonthly, commute: $input.njCommuteMonthly)
                    }
                    if let error { ErrorLine(message: error) }
                    if ready, let nyc, let nj {
                        Text(L("住新泽西每年可结余差额：{d}", ["d": m0(nj.netIncome - input.spending(residence: "nj") - nyc.netIncome + input.spending(residence: "nyc"))]))
                            .font(.ui(15, .medium)).foregroundStyle(Theme.ink)
                        Text(L("正数表示新泽西可结余更多；已纳入你填写的住房和交通差额。未包含搬家成本。")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                    }
                    Text(L("新泽西居民仍缴纽约来源工资的纽约州税；新泽西税额按同一收入的限额抵免计算。LIC 属于纽约市，另有纽约市居民税。")).font(.ui(12)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                    Text(L("此处按全年同一居住地、单份纽约工资估算；跨年搬家、多份工资、投资收入或其他复杂申报仍需另行核对。")).font(.ui(12)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                    DisclosureRow(title: L("计算明细与官方依据"), open: $details)
                    if details { breakdown }
                    }
                }.padding(24)
            }.id(context)
            Hairline()
            Text(context == "china_resident" ? L("中国金额按你输入的规划汇率换算为美元，再接入现有投资路径。中国情景不继续新增美国 401(k) 工资缴款，原有退休余额仍保留。") : L("应用后，现金到手收入与未来 401(k) 缴款分开进入规划；请再确认退休资金可提取时间和预计提取税率。")).font(.ui(11.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true).padding(.horizontal, 24).padding(.top, 14)
            HStack {
                Text(L("只在点击应用时更新当前计划。")).font(.ui(12)).foregroundStyle(Theme.muted)
                Spacer()
                if context == "china_resident" {
                    Button(L("应用中国情景")) { applyChina() }.buttonStyle(.keelPrimaryCompact).disabled(!chinaReady)
                } else {
                Button(L("应用 LIC 情景")) { apply("nyc") }.buttonStyle(.keelOutlineCompact).disabled(!ready)
                Button(L("应用新泽西情景")) { apply("nj") }.buttonStyle(.keelPrimaryCompact).disabled(!ready)
                }
            }.padding(24)
        }
        .frame(width: 900, height: 780).background(Theme.card)
        .task(id: taxKey) { await calculate() }
    }

    private func optional(_ key: WritableKeyPath<EmploymentComparisonSettings, Double?>) -> Binding<Double> {
        Binding(get: { input[keyPath: key] ?? 0 }, set: { input[keyPath: key] = $0 })
    }

    private var chinaForm: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(L("所有金额先填人民币。社保、公积金及专项扣除填写你实际符合条件的全年金额，不猜测城市缴费比例；单独计税奖金及其他综合所得暂不合并处理。")).font(.ui(12)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 20) {
                inputField(L("税前年工资与并入综合所得的奖金（人民币）"), value: optional(\.chinaGrossIncome))
                inputField(L("个人年社保缴款"), value: optional(\.socialInsuranceAnnual))
            }
            HStack(spacing: 20) {
                inputField(L("个人年公积金缴款"), value: optional(\.housingFundAnnual))
                inputField(L("全年专项附加扣除"), value: optional(\.specialDeductionsAnnual))
                inputField(L("全年其他合格扣除"), value: optional(\.otherDeductionsAnnual))
            }
            HStack(spacing: 20) {
                inputField(L("预计年开销（人民币）"), value: optional(\.chinaAnnualSpending))
                inputField(L("规划汇率：1 美元兑换人民币"), value: optional(\.cnyPerUSD))
            }
            if let china, computedKey == taxKey {
                HStack(spacing: 30) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(L("每月现金到手（人民币）")).font(.ui(12)).foregroundStyle(Theme.muted)
                        Text("CNY " + String(format: "%.0f", china.netIncome / 12)).font(.display(32))
                    }
                    VStack(alignment: .leading, spacing: 6) {
                        Text(L("全年个人所得税（人民币）")).font(.ui(12)).foregroundStyle(Theme.muted)
                        Text("CNY " + String(format: "%.0f", china.incomeTax)).font(.display(26))
                    }
                }
                Text(L("全年应纳税所得额 {a}，个人社保和公积金 {c}。基本减除费用每年人民币 60,000 元。", ["a": String(format: "%.2f", china.taxableIncome), "c": String(format: "%.2f", china.employeeContributions)])).font(.ui(12)).foregroundStyle(Theme.muted)
                if (input.cnyPerUSD ?? 0) > 0 {
                    Text(L("按规划汇率折算：年税后收入 {i}，年开销 {e}。", ["i": m0(china.netIncome / (input.cnyPerUSD ?? 1)), "e": m0((input.chinaAnnualSpending ?? 0) / (input.cnyPerUSD ?? 1))])).font(.ui(13, .medium))
                }
                ForEach(Array(china.sources.enumerated()), id: \.offset) { i, source in
                    if let url = URL(string: source) { Button(L("官方依据 {n} · {host}", ["n": i + 1, "host": url.host ?? source]) + " ↗") { NSWorkspace.shared.open(url) }.buttonStyle(.keelOutlineCompact) }
                }
            }
            if let error { ErrorLine(message: error) }
        }
    }

    private func applyChina() {
        guard chinaReady, let china else { return }
        let rate = input.cnyPerUSD ?? 0
        guard rate > 0 else { return }
        store.update {
            $0.scenario.annualIncome = china.netIncome / rate
            $0.scenario.annualSpending = (input.chinaAnnualSpending ?? 0) / rate
            $0.scenario.annualRetirementContribution = 0
            $0.cashflowReviewed = true
            $0.employmentComparison = input
        }
        dismiss()
    }

    private func inputField(_ title: String, id: String? = nil, value: Binding<Double>) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.ui(12)).foregroundStyle(Theme.muted)
            PlanningAmountField(value: value.wrappedValue, fieldID: id ?? title, invalidFields: $invalidFields) { value.wrappedValue = $0 }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private func residenceCard(_ title: String, residence: String, result: EmploymentTaxResult?, housing: Binding<Double>, commute: Binding<Double>) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title).font(.ui(15, .medium))
            Text(result.map { m0($0.netIncome / 12) } ?? "—").font(.display(32)).opacity(ready ? 1 : 0.45)
            Text(L("每月现金到手 · 已扣税与 401(k)")).font(.ui(11.5)).foregroundStyle(Theme.muted)
            Hairline()
            inputField(L("每月住房成本"), id: residence + "-housing", value: housing)
            inputField(L("每月交通与通勤成本"), id: residence + "-transport", value: commute)
            Hairline()
            HStack {
                Text(L("每月可结余")).font(.ui(12)).foregroundStyle(Theme.muted)
                Spacer()
                Text(result.map { m0(($0.netIncome - input.spending(residence: residence)) / 12) } ?? "—").font(.mono(16)).opacity(ready ? 1 : 0.45)
            }
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.fill.opacity(0.5), in: RoundedRectangle(cornerRadius: 10))
    }

    private var breakdown: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let a = nyc, let b = nj {
                HStack { Text(L("年度税额")); Spacer(); Text("LIC").frame(width: 135); Text(L("新泽西")).frame(width: 135) }.font(.ui(12, .medium))
                taxLine(L("联邦所得税"), a.federalTax, b.federalTax)
                taxLine(L("Social Security"), a.socialSecurity, b.socialSecurity)
                taxLine(L("Medicare（含附加税）"), a.medicare + a.additionalMedicare, b.medicare + b.additionalMedicare)
                taxLine(L("纽约州所得税"), a.nyStateTax, b.nyStateTax)
                taxLine(L("纽约市税（扣除学校税抵免）"), a.nycTax, b.nycTax)
                taxLine(L("新泽西抵免前税额"), a.njTaxBeforeCredit, b.njTaxBeforeCredit)
                taxLine(L("新泽西外州税抵免"), -a.njCredit, -b.njCredit)
                taxLine(L("合计税额"), a.totalTax, b.totalTax)
                Text(context == "us_nonresident" ? L("已计入你填写的合格逐项扣除、协定免税额及 FICA 选项；其他抵免、雇员医保和州工资保险扣款未计入。纽约市学校税抵免超过市税的可退款部分不计入到手收入。") : L("包含纽约州高收入税率补算；不含逐项扣除、联邦及州其他抵免、雇员医保和州工资保险扣款。纽约市学校税抵免超过市税的可退款部分不计入到手收入。")).font(.ui(11.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                ForEach(Array(a.sources.enumerated()), id: \.offset) { i, source in
                    if let url = URL(string: source) {
                        Button(L("官方依据 {n} · {host}", ["n": i + 1, "host": url.host ?? source]) + " ↗") { NSWorkspace.shared.open(url) }.buttonStyle(.keelOutlineCompact)
                    }
                }
            }
        }
    }

    private func taxLine(_ title: String, _ a: Double, _ b: Double) -> some View {
        HStack { Text(title).font(.ui(12)); Spacer(); Text(m0(a)).frame(width: 135, alignment: .trailing); Text(m0(b)).frame(width: 135, alignment: .trailing) }.font(.mono(12))
    }

    private func calculate() async {
        guard let api = model.api else { return }
        let snapshot = input
        let key = taxKey
        error = nil
        computedKey = nil
        try? await Task.sleep(for: .milliseconds(300))
        guard !Task.isCancelled else { return }
        do {
            if context == "china_resident" {
                let result = try await api.chinaEmploymentTax(snapshot)
                guard !Task.isCancelled, key == taxKey else { return }
                china = result; computedKey = key
                return
            }
            async let first = context == "us_nonresident" ? api.nonresidentEmploymentTax(snapshot, residence: "nyc") : api.employmentTax(EmploymentTaxInput(snapshot, residence: "nyc"))
            async let second = context == "us_nonresident" ? api.nonresidentEmploymentTax(snapshot, residence: "nj") : api.employmentTax(EmploymentTaxInput(snapshot, residence: "nj"))
            let pair = try await (first, second)
            guard !Task.isCancelled, key == taxKey else { return }
            nyc = pair.0; nj = pair.1; computedKey = key
        } catch {
            if !Task.isCancelled, key == taxKey { self.error = error.localizedDescription }
        }
    }

    private func apply(_ residence: String) {
        guard ready, let result = residence == "nj" ? nj : nyc else { return }
        store.update {
            $0.scenario.annualIncome = result.netIncome
            $0.scenario.annualSpending = input.spending(residence: residence)
            $0.cashflowReviewed = true
            if ($0.scenario.annualRetirementContribution ?? 0) != input.retirementContribution {
                $0.scenario.annualRetirementContribution = input.retirementContribution
                $0.retirementReviewed = false
            }
            $0.employmentComparison = input
        }
        dismiss()
    }
}
