import AppKit
import KeelCore
import SwiftUI

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var prefs: Prefs?
    @State private var budget = ""
    @State private var floor = ""
    @State private var saved: String?
    @State private var launchAtLogin = false

    var body: some View {
        Page(width: 672) {
            VStack(alignment: .leading, spacing: 0) {
                Text(L("设置")).font(.display(34)).foregroundStyle(Theme.ink).padding(.bottom, 36)

                SettingsSection(title: L("外观"), hint: L("跟随系统时，macOS 切换深色模式 Keel 也跟着变。")) {
                    Segmented(
                        items: [("system", L("跟随系统")), ("light", L("浅色")), ("dark", L("深色"))],
                        selection: Binding(get: { model.theme }, set: { model.setTheme($0) })
                    )
                }
                SettingsSection(title: L("语言"), hint: L("默认跟随系统语言。提醒和月报通知也用这个语言。")) {
                    Segmented(
                        items: [("system", L("跟随系统")), ("zh", "中文"), ("en", "English")],  // l10n-allow: each in its own language
                        selection: Binding(get: { L10n.preference }, set: { model.setLanguage($0) })
                    )
                }
                SettingsSection(title: L("预算和提醒"), hint: L("「现在」页的超预算提醒按这个预算计算；不填就按你平时一个月的花销。")) {
                    VStack(alignment: .leading, spacing: 14) {
                        FieldRow(label: L("每月预算（主币种）")) {
                            KeelField(placeholder: L("留空 = 平时一个月"), text: $budget, width: 160, mono: true)
                        }
                        FieldRow(label: L("现金最低余额（主币种）")) {
                            KeelField(placeholder: "0", text: $floor, width: 160, mono: true)
                            Text(L("预计现金账户会低于它时提醒你")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                        }
                        Toggle(isOn: Binding(get: { prefs?.alerts ?? true }, set: { on in Task { await save { $0.alerts = on } } })) {
                            Text(L("有新提醒时发系统通知（超支、工资没到账、现金不够、年费续费）")).font(.ui(13)).foregroundStyle(Theme.ink)
                        }
                        .toggleStyle(.checkbox)
                        HStack(spacing: 12) {
                            Button(L("保存")) {
                                Task {
                                    await save {
                                        $0.monthlyBudget = budget.trimmingCharacters(in: .whitespaces).isEmpty ? nil : budget
                                        $0.lowBalance = floor.isEmpty ? "0" : floor
                                    }
                                }
                            }
                            .buttonStyle(.keelPrimary)
                            if let saved { Text(saved).font(.ui(12)).foregroundStyle(Theme.muted) }
                        }
                    }
                }
                SettingsSection(title: L("启动"), hint: L("开机后 Keel 在菜单栏待命，自动导入下载的账单、按时提醒。")) {
                    VStack(alignment: .leading, spacing: 10) {
                        Toggle(isOn: $launchAtLogin) { Text(L("开机自动启动")).font(.ui(13)).foregroundStyle(Theme.ink) }
                            .toggleStyle(.checkbox)
                            .onChange(of: launchAtLogin) { _, on in
                                if on != model.launchAtLogin { model.launchAtLogin = on }
                            }
                        Toggle(isOn: Binding(get: { prefs?.backgroundSync ?? true }, set: { on in Task { await save { $0.backgroundSync = on } } })) {
                            Text(L("Keel 没打开时，也在每天早上 7:30 同步银行和券商")).font(.ui(13)).foregroundStyle(Theme.ink)
                        }
                        .toggleStyle(.checkbox)
                        Text(L("Mac 那时在睡眠，就在唤醒后补上；关机就在下次登录时补上。macOS 会在「登录项」里把它列为后台项目。"))
                            .font(.ui(11.5)).foregroundStyle(Theme.muted)
                        Toggle(isOn: Binding(get: { prefs?.autoClose ?? true }, set: { on in Task { await save { $0.autoClose = on } } })) {
                            Text(L("上个月的账齐了、对上了，就自动结账")).font(.ui(13)).foregroundStyle(Theme.ink)
                        }
                        .toggleStyle(.checkbox)
                        Text(L("结账是记下那个月当时的收支；之后数字再变会提醒你。你手动重新打开的月份不会被自动结掉。"))
                            .font(.ui(11.5)).foregroundStyle(Theme.muted)
                    }
                }
                PlanningAssumptionsSection()
                OwnerNamesSection()
                AdvancedSection()
                UpdateSection()
                DataSection(prefs: prefs) { on in Task { await save { $0.backupIcloud = on } } }
            }
        }
        .task {
            launchAtLogin = model.launchAtLogin
            guard let api = model.api, let p = try? await api.prefs() else { return }
            prefs = p
            budget = p.monthlyBudget ?? ""
            floor = p.lowBalance
        }
    }

    private func save(_ change: (inout Prefs) -> Void) async {
        guard let api = model.api, var p = prefs else { return }
        change(&p)
        do {
            prefs = try await api.savePrefs(p)
            saved = "✓ " + L("已保存")
            model.revision += 1
        } catch {
            saved = L("没保存：请输入不小于 0 的数字。")
        }
    }
}

/// Names that are you: a wallet transfer to one of them moves your own money (your other Alipay
/// or WeChat login) and is not counted as spending. Names from statements' headers come in on
/// their own; add the ones a statement doesn't state.
private struct OwnerNamesSection: View {
    @Environment(AppModel.self) private var model
    @State private var names: [OwnerName] = []
    @State private var draft = ""

    var body: some View {
        SettingsSection(
            title: L("这些名字是你"),
            hint: L("转给这些名字的钱算作你自己账户之间的转账，不算花销。比如你另一个支付宝账号的实名。账单里带的名字会自动加进来。")
        ) {
            VStack(alignment: .leading, spacing: 12) {
                if !names.isEmpty {
                    VStack(spacing: 0) {
                        ForEach(names) { n in
                            HStack {
                                Text(n.name).font(.ui(13)).foregroundStyle(Theme.ink)
                                Text(n.source == "statement" ? L("来自账单") : L("你添加的")).font(.ui(11)).foregroundStyle(Theme.muted)
                                Spacer()
                                Button(L("删除")) { Task { await run { try await $0.deleteOwnerName(n.name) } } }
                                    .buttonStyle(.keelOutlineCompact)
                            }
                            .padding(.vertical, 8)
                            Hairline()
                        }
                    }
                }
                HStack(spacing: 10) {
                    KeelField(placeholder: L("名字，比如 李明"), text: $draft, width: 220)
                    Button(L("添加")) {
                        let name = draft.trimmingCharacters(in: .whitespaces)
                        guard !name.isEmpty else { return }
                        draft = ""
                        Task { await run { try await $0.addOwnerName(name) } }
                    }
                    .buttonStyle(.keelOutline)
                    .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
        .task { await run { try await $0.ownerNames() } }
    }

    private func run(_ call: (API) async throws -> [OwnerName]) async {
        guard let api = model.api, let list = try? await call(api) else { return }
        let changed = list != names && !names.isEmpty
        names = list
        if changed { model.revision += 1 }  // transfers may have been re-matched
    }
}

/// Where the ledger lives, its nightly backups (and whether they go to iCloud Drive).
private struct DataSection: View {
    @Environment(AppModel.self) private var model
    let prefs: Prefs?
    let setICloud: (Bool) -> Void
    @State private var status: BackupStatus?
    @State private var working = false

    var body: some View {
        SettingsSection(title: L("你的数据"), hint: L("账单和账本只存在这台电脑上，除非你打开下面的 iCloud 备份。每天自动备份一次，保留最近 7 天、8 周、12 个月和每年的一份。")) {
            VStack(alignment: .leading, spacing: 14) {
                if let s = status {
                    Text(s.last.map { L("上次备份：{t}，共 {n} 份", ["t": $0.replacingOccurrences(of: "T", with: " "), "n": s.count]) } ?? L("还没有备份"))
                        .font(.ui(13)).foregroundStyle(Theme.ink)
                    Text(s.dir).font(.mono(11)).foregroundStyle(Theme.muted).textSelection(.enabled)
                }
                Toggle(isOn: Binding(get: { prefs?.backupIcloud ?? false }, set: { on in setICloud(on); Task { await refresh() } })) {
                    Text(L("备份存到 iCloud 云盘（换电脑、硬盘坏了也找得回来）")).font(.ui(13)).foregroundStyle(Theme.ink)
                }
                .toggleStyle(.checkbox)
                .disabled(!(status?.icloudAvailable ?? false))
                if status?.icloudAvailable == false {
                    Text(L("这台 Mac 没有打开 iCloud 云盘。")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                }
                HStack(spacing: 10) {
                    Button(working ? L("正在备份…") : L("立即备份")) {
                        Task {
                            working = true
                            defer { working = false }
                            if let api = model.api, let s = try? await api.backupNow() { status = s }
                        }
                    }
                    .buttonStyle(.keelOutline)
                    .disabled(working)
                    Button(L("在 Finder 中显示")) {
                        let home = FileManager.default.homeDirectoryForCurrentUser
                            .appendingPathComponent("Library/Application Support/Keel")
                        NSWorkspace.shared.activateFileViewerSelecting([status.map { URL(fileURLWithPath: $0.dir) } ?? home])
                    }
                    .buttonStyle(.keelOutline)
                }
            }
        }
        .task { await refresh() }
    }

    private func refresh() async {
        try? await Task.sleep(for: .milliseconds(300))  // after the preference is saved
        if let api = model.api, let s = try? await api.backupStatus() { status = s }
    }
}

/// A settings block: hairline above, serif title, one line of explanation, the controls.
struct SettingsSection<Content: View>: View {
    let title: String
    var hint: String?
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Hairline()
            Text(title).font(.display(19)).foregroundStyle(Theme.ink).padding(.top, 28)
            if let hint {
                Text(hint).font(.ui(13)).foregroundStyle(Theme.muted).padding(.top, 6)
                    .fixedSize(horizontal: false, vertical: true)
            }
            content.padding(.top, 16).padding(.bottom, 36)
        }
    }
}

private struct FieldRow<Content: View>: View {
    let label: String
    @ViewBuilder var content: Content

    var body: some View {
        HStack(spacing: 14) {
            Text(label).font(.ui(13)).foregroundStyle(Theme.ink).frame(width: 178, alignment: .leading)
            content
        }
    }
}

/// 版本与更新: the commit this app was built from, what's new on its branch, and an update that
/// pulls, rebuilds and relaunches Keel (engine side: backend/pf/ops/update.py).
struct UpdateSection: View {
    @Environment(AppModel.self) private var model
    @State private var version: VersionInfo?
    @State private var check: UpdateCheck?
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        SettingsSection(title: L("版本与更新")) {
          VStack(alignment: .leading, spacing: 12) {
            // The program's Appropriate Legal Notices (AGPL-3.0, NOTICE): keep in any work based on it.
            HStack(spacing: 6) {
                Text(L("Keel 龙骨 · realAllenSong 作品 · 开源许可 AGPL-3.0")).font(.ui(12)).foregroundStyle(Theme.muted)
                Link(L("源代码"), destination: URL(string: "https://github.com/realAllenSong/Keel")!)
                    .font(.ui(12))
            }
            if let v = version, v.channel == "release" {
                Text(L("当前版本 {v}", ["v": v.version ?? "?"])).font(.ui(13)).foregroundStyle(Theme.muted)
            } else if let v = version {
                Text(L("当前版本 {c}（{b} 分支）{d}", [
                    "c": String((v.commit ?? "?").prefix(7)),
                    "b": v.branch ?? "?",
                    "d": v.builtAt.map { L("，构建于 {d}", ["d": String($0.prefix(16)).replacingOccurrences(of: "T", with: " ")]) } ?? "",
                ]))
                .font(.ui(13))
                .foregroundStyle(Theme.muted)
                if v.status["state"] == "running" {
                    Label(stepText(v.status["step"]) + " " + L("完成后 Keel 会自动重新打开。"), systemImage: "arrow.triangle.2.circlepath")
                } else if v.status["state"] == "failed" {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(L("上次更新在「{step}」这一步失败，正在用的版本没有变化。", ["step": v.status["step"] ?? "?"]))
                            .foregroundStyle(Color.outflow)
                        if let log = v.status["log"] {
                            ScrollView { Text(log).font(.mono(11)).textSelection(.enabled) }.frame(maxHeight: 120)
                        }
                    }
                }
            }
            HStack(spacing: 10) {
                Button(busy ? L("正在检查…") : L("检查更新")) { Task { await runCheck() } }
                    .buttonStyle(.keelOutline)
                    .disabled(busy)
                if check?.canApply == true {
                    Button(L("更新并重启")) { Task { await runApply() } }
                        .buttonStyle(.keelPrimary)
                        .disabled(busy)
                }
            }
            if let c = check {
                if let e = c.error {
                    Text(L("暂时连不上更新服务器：{e}", ["e": e])).foregroundStyle(Color.outflow)
                } else if c.behind > 0 {
                    Text(version?.channel == "release" ? L("有新版本 {v}：", ["v": c.latest ?? ""]) : L("有 {n} 项更新：", ["n": c.behind]))
                    ForEach(c.changes) { change in
                        HStack(alignment: .firstTextBaseline) {
                            Text(change.date).font(.mono(11.5)).foregroundStyle(Theme.muted)
                            Text(change.subject)
                        }
                    }
                    if let r = c.reason { Text(reasonText(r)).foregroundStyle(Theme.muted) }
                    if c.canApply {
                        Text(version?.channel == "release"
                            ? L("更新前会先备份账本；新版本下载并校验签名之后才会替换，旧版本会保留。")
                            : L("更新前会先备份账本；新版本构建好之前，Keel 照常可用。")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                    }
                } else {
                    Text(reasonText(c.reason ?? "up_to_date")).foregroundStyle(Theme.muted)
                }
            }
            if let error { Text(error).foregroundStyle(Color.outflow) }
          }
          .font(.ui(13))
          .foregroundStyle(Theme.ink)
        }
        .task { await refresh() }
        .task(id: version?.status["state"]) {
            // Poll while an update builds; the app is replaced and reopened when it finishes.
            while version?.status["state"] == "running" {
                try? await Task.sleep(for: .seconds(2))
                await refresh()
            }
        }
    }

    private func refresh() async {
        guard let api = model.api else { return }
        version = try? await api.version()
    }

    private func runCheck() async {
        guard let api = model.api else { return }
        busy = true
        defer { busy = false }
        do {
            check = try await api.checkUpdate()
            error = nil
        } catch {
            self.error = L("检查失败，请稍后再试。")
        }
    }

    private func runApply() async {
        guard let api = model.api else { return }
        busy = true
        defer { busy = false }
        do {
            _ = try await api.applyUpdate()
            await refresh()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func stepText(_ step: String?) -> String {
        switch step {
        case "download": return L("正在下载新版本…")
        case "verify": return L("正在校验签名…")
        case "pull": return L("正在拉取新代码…")
        case "build": return L("正在构建新版本（几分钟）…")
        case "restart": return L("正在重启…")
        default: return L("准备中…")
        }
    }

    private func reasonText(_ reason: String) -> String {
        switch reason {
        case "up_to_date": return L("已经是最新版本。")
        case "local_changes": return L("代码目录里有未提交的改动，先提交或撤销再更新。")
        case "diverged": return L("本地分支和远端分叉了，需要先手动合并。")
        case "source_mode": return L("正在用开发模式运行：在终端里 git pull 后重新运行 make dev 即可。")
        case "no_repo": return L("找不到构建这个应用的代码目录。")
        case "detached": return L("构建时不在任何分支上，无法判断有没有更新。")
        case "no_asset": return L("新版本还在发布中，稍后再试。")
        default: return reason
        }
    }
}

/// 规划假设: the main currency and what plans, lighthouses and idle-cash notes assume. Saved by the
/// app (no files to edit); 恢复默认 forgets every change.
struct PlanningAssumptionsSection: View {
    @Environment(AppModel.self) private var model
    @State private var loaded: AssumptionsOut?
    @State private var edit: Assumptions?
    @State private var returns: [String: String] = [:]
    @State private var withdrawal = ""
    @State private var idle = ""
    @State private var message: String?

    /// Asset classes you can set a return for (dictionary keys travel as sent: term_deposit).
    private static let classes: [(key: String, label: String)] = [
        ("cash", L("现金")), ("term_deposit", L("定期存款")), ("brokerage", L("券商")),
        ("retirement", L("退休账户")), ("crypto", L("加密")), ("real_estate", L("房产")),
    ]

    var body: some View {
        SettingsSection(title: L("规划假设"), hint: L("规划、灯塔和闲置现金提醒都按这些假设计算。改完点保存，立即生效。")) {
            if let edit {
                VStack(alignment: .leading, spacing: 14) {
                    FieldRow(label: L("主币种")) {
                        Segmented(
                            items: (loaded?.currencies ?? ["USD", "CNY"]).map { ($0, $0 == "USD" ? L("美元") : L("人民币")) },
                            selection: Binding(get: { edit.baseCurrency }, set: { self.edit?.baseCurrency = $0 })
                        )
                    }
                    Text(L("目前支持美元和人民币。其他币种的账户暂时无法正确换算。"))
                        .font(.ui(11.5)).foregroundStyle(Theme.muted)
                    Text(L("预期年回报（%）")).font(.ui(12, .medium)).foregroundStyle(Theme.ink2).padding(.top, 6)
                    ForEach(Self.classes, id: \.key) { c in
                        FieldRow(label: c.label) {
                            KeelField(placeholder: "0", text: binding(c.key), width: 100, mono: true)
                            Toggle(isOn: fiBinding(c.key)) { Text(L("计入财务自由")).font(.ui(12)).foregroundStyle(Theme.muted) }
                                .toggleStyle(.checkbox)
                        }
                    }
                    FieldRow(label: L("每年提取比例（%）")) {
                        KeelField(placeholder: "4", text: $withdrawal, width: 100, mono: true)
                        Text(L("第三座灯塔：资产 × 这个比例 ≥ 一年开销")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                    }
                    FieldRow(label: L("闲置现金（月开销）")) {
                        KeelField(placeholder: "6", text: $idle, width: 100, mono: true)
                        Text(L("现金超过这么多个月的开销时提醒你")).font(.ui(11.5)).foregroundStyle(Theme.muted)
                    }
                    HStack(spacing: 12) {
                        Button(L("保存")) { Task { await save() } }.buttonStyle(.keelPrimary)
                        Button(L("恢复默认")) { Task { await reset() } }.buttonStyle(.keelOutline)
                        if let message { Text(message).font(.ui(12)).foregroundStyle(Theme.muted) }
                    }
                }
            } else {
                Text(L("读取中…")).font(.ui(12)).foregroundStyle(Theme.muted)
            }
        }
        .task { await load() }
    }

    private func binding(_ key: String) -> Binding<String> {
        Binding(get: { returns[key] ?? "" }, set: { returns[key] = $0 })
    }

    private func fiBinding(_ wire: String) -> Binding<Bool> {
        Binding(
            get: { edit?.fiAssetClasses.contains(wire) ?? false },
            set: { on in
                guard var e = edit else { return }
                e.fiAssetClasses.removeAll { $0 == wire }
                if on { e.fiAssetClasses.append(wire) }
                edit = e
            }
        )
    }

    private func show(_ out: AssumptionsOut) {
        loaded = out
        edit = out.current
        returns = Dictionary(uniqueKeysWithValues: Self.classes.map { ($0.key, Self.percent(out.current.expectedReturn[$0.key] ?? 0)) })
        withdrawal = Self.percent(out.current.withdrawalRate)
        idle = Self.number(out.current.idleCashMonths)
    }

    private static func percent(_ v: Double) -> String { number(v * 100) }
    private static func number(_ v: Double) -> String {
        v == v.rounded() ? String(Int(v)) : String(format: "%g", (v * 1000).rounded() / 1000)
    }

    private func load() async {
        guard let api = model.api, let out = try? await api.assumptions() else { return }
        show(out)
    }

    private func save() async {
        guard let api = model.api, var e = edit else { return }
        var parsed: [String: Double] = [:]
        for c in Self.classes {
            guard let v = Double((returns[c.key] ?? "").trimmingCharacters(in: .whitespaces)) else {
                message = L("{c} 的回报不是数字", ["c": c.label]); return
            }
            parsed[c.key] = v / 100
        }
        guard let w = Double(withdrawal), let m = Double(idle) else { message = L("请填写数字"); return }
        e.expectedReturn = parsed
        e.withdrawalRate = w / 100
        e.idleCashMonths = m
        do {
            show(try await api.saveAssumptions(e))
            message = L("已保存")
            model.revision += 1
        } catch {
            message = L("没有保存：{e}", ["e": error.localizedDescription])
        }
    }

    private func reset() async {
        guard let api = model.api, let out = try? await api.resetAssumptions() else { return }
        show(out)
        message = L("已恢复默认")
        model.revision += 1
    }
}


/// 高级: bank and broker connections (Plaid, SnapTrade, IBKR Flex, OKX) and mailbox reading. They
/// need developer keys or app passwords, so most people never see them; the everyday path is
/// downloading statements. Open by default once anything is connected.
struct AdvancedSection: View {
    @Environment(AppModel.self) private var model
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SettingsSection(title: L("高级"), hint: L("大多数人不需要这里：下载账单拖进 Keel，或放进「下载」文件夹，就会自动导入。")) {
                Toggle(isOn: $open) {
                    Text(L("显示银行直连和邮箱读取（需要开发者密钥或邮箱应用密码）")).font(.ui(13)).foregroundStyle(Theme.ink)
                }
                .toggleStyle(.checkbox)
            }
            if open { ConnectionsSection() }
        }
        .task {
            guard let api = model.api, let list = try? await api.connections() else { return }
            if list.contains(where: \.connected) { open = true }
        }
    }
}
