import KeelCore
import SwiftUI

/// 存钱目标 on the planning page (not the simulation's net-worth milestones in the inspector): what you are saving toward, how far along, at what pace, and
/// whether the date holds. A goal counts the accounts linked to it, or net worth when none is.
struct GoalsSection: View {
    @Environment(AppModel.self) private var model
    @State private var goals = Loader<[Goal]>("plan.goals")
    @State private var accounts: [LedgerAccount] = []
    @State private var adding = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Text(L("存钱目标")).font(.serif(22)).foregroundStyle(Theme.ink)
                Text(L("按账户里实际的钱算进度")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                Spacer()
                LinkButton(title: adding ? L("取消") : L("添加目标"), arrow: false) { adding.toggle() }
            }
            if adding {
                GoalEditor(accounts: accounts) { body in
                    change { try await $0.addGoal(body) }
                    adding = false
                }
            }
            if let list = goals.value {
                if list.isEmpty && !adding {
                    Text(L("给一笔钱定个去处：应急金、首付、一次旅行。Keel 按你最近每月存下的速度，算出什么时候够。"))
                        .font(.ui(13)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                }
                ForEach(list) { goal in
                    Hairline(color: Theme.rule)
                    row(goal)
                }
            }
        }
        .flatSection()
        .task(id: model.revision) {
            await goals.load { try await $0.goals() }
            if let api = model.api, let list = try? await api.accounts() {
                accounts = list.filter { $0.type == "Assets" && $0.flowOnly != true }
            }
        }
    }

    private func row(_ g: Goal) -> some View {
        let part = min(1, max(0, g.progress))
        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(g.name).font(.ui(14, .medium)).foregroundStyle(Theme.ink)
                Text(g.accounts.isEmpty ? L("按净资产算") : g.accounts.map { Format.account($0) }.joined(separator: L("、")))
                    .font(.ui(11.5)).foregroundStyle(Theme.muted).lineLimit(1)
                Spacer()
                Text(Format.money(g.saved, currency: g.currency) + " / " + Format.money(g.target, currency: g.currency))
                    .font(.mono(13)).foregroundStyle(Theme.ink)
                Text(Format.percent(g.progress)).font(.mono(12)).foregroundStyle(Theme.muted).frame(width: 44, alignment: .trailing)
                LinkButton(title: L("删除"), arrow: false) { change { try await $0.deleteGoal(g.id) } }
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Rectangle().fill(Theme.fill).frame(height: 3)
                    Rectangle().fill(Theme.ink).frame(width: max(2, geo.size.width * part), height: 3)
                }
            }
            .frame(height: 3)
            HStack(spacing: 6) {
                Text(pace(g)).font(.ui(12)).foregroundStyle(Theme.muted)
                if let verdict = verdict(g) {
                    Text("·").font(.ui(12)).foregroundStyle(Theme.muted)
                    Text(verdict.text).font(.ui(12)).foregroundStyle(verdict.late ? Theme.signal : Theme.muted)
                }
            }
        }
        .padding(.vertical, 4)
    }

    private func pace(_ g: Goal) -> String {
        if g.reached { return L("已经达成") }
        let pace = Format.money(g.monthlyPace, currency: g.currency, sign: true)
        if let eta = g.eta {
            return L("最近每月 {p}，照这个速度 {m} 达成", ["p": pace, "m": Format.month(eta)])
        }
        return L("最近每月 {p}，照这个速度到不了", ["p": pace])
    }

    private func verdict(_ g: Goal) -> (text: String, late: Bool)? {
        guard !g.reached, let due = g.due, let need = g.neededPerMonth, let ok = g.onTrack else { return nil }
        let day = Format.day(due) + (due.prefix(4) == model.today.prefix(4) ? "" : " " + due.prefix(4))
        let text = L("要在 {d} 前达成，每月需要 {n}", ["d": day, "n": Format.money(need, currency: g.currency)])
        return (ok ? text + L("，来得及") : text + L("，现在的速度不够"), !ok)
    }

    private func change(_ call: @escaping (API) async throws -> [Goal]) {
        guard let api = model.api else { return }
        Task {
            do { goals.set(try await call(api)) } catch { model.banner = L("没保存：{e}", ["e": error.localizedDescription]) }
        }
    }
}

private struct GoalEditor: View {
    let accounts: [LedgerAccount]
    let save: (GoalIn) -> Void
    @State private var name = ""
    @State private var target = ""
    @State private var currency = "USD"
    @State private var dated = false
    @State private var due = Calendar.current.date(byAdding: .month, value: 6, to: Date()) ?? Date()
    @State private var picked: Set<String> = []

    private var amount: Decimal? { parseAmount(target).flatMap { $0 > 0 ? $0 : nil } }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                KeelField(placeholder: L("名字，比如 应急金"), text: $name, width: 220)
                KeelField(placeholder: L("目标金额"), text: $target, width: 140, mono: true)
                ReportCurrencyPicker(currency: $currency)
            }
            HStack(spacing: 12) {
                Toggle(isOn: $dated) { Text(L("想在某天前达成")).font(.ui(13)).foregroundStyle(Theme.ink) }.toggleStyle(.checkbox)
                if dated { DatePicker("", selection: $due, in: Date()..., displayedComponents: .date).labelsHidden().fixedSize() }
            }
            HStack(alignment: .top, spacing: 12) {
                Menu {
                    ForEach(accounts) { a in
                        Button {
                            if picked.contains(a.name) { picked.remove(a.name) } else { picked.insert(a.name) }
                        } label: {
                            Label(Format.account(a.name), systemImage: picked.contains(a.name) ? "checkmark" : "")
                        }
                    }
                } label: {
                    Text(picked.isEmpty ? L("哪些账户里的钱算数：全部（净资产）") : L("算这 {n} 个账户里的钱", ["n": picked.count]))
                        .font(.ui(13))
                }
                .menuStyle(.borderlessButton).fixedSize()
                Spacer()
                Button(L("保存")) {
                    guard let amount else { return }
                    let f = DateFormatter()
                    f.calendar = Calendar(identifier: .gregorian)
                    f.locale = Locale(identifier: "en_US_POSIX")
                    f.dateFormat = "yyyy-MM-dd"
                    save(GoalIn(
                        name: name.trimmingCharacters(in: .whitespaces), target: amountString(amount), currency: currency,
                        due: dated ? f.string(from: due) : nil, accounts: picked.sorted()
                    ))
                }
                .buttonStyle(.keelPrimaryCompact)
                .disabled(amount == nil || name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(.bottom, 6)
    }
}
