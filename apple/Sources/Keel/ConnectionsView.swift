import AppKit
import KeelCore
import SwiftUI

/// Settings → 连接账户: paste each provider's keys once; Keel then syncs every day (engine side:
/// backend/pf/connectors). Keys go to the Keychain through the engine and never come back here.
struct ConnectionsSection: View {
    @Environment(AppModel.self) private var model
    @State private var connections: [AccountConnection] = []
    @State private var error: String?
    @State private var showingDownloads = false

    var body: some View {
        SettingsSection(
            title: L("连接账户"),
            hint: L("Keel 运行期间，每天本机时间 07:30 后自动同步一次；晚于此时启动会补一次。关闭窗口后仍可在菜单栏运行，退出 App 则停止同步。")
        ) {
            VStack(alignment: .leading, spacing: 0) {
                DisclosureGroup(L("同步如何工作")) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(L("每个连接显示的是 Keel 读取数据的时间。银行或券商的数据可能更早；「立即同步」也要等上游提供新数据。"))
                        Text(L("每日任务失败后可点对应账户的「立即同步」。邮箱每天读取已发来的账单；新账期仍在原 App 申请。"))
                    }
                    .font(.ui(12)).foregroundStyle(Theme.muted).padding(.top, 6)
                }
                .font(.ui(12)).padding(.vertical, 12)
                ForEach(connections) { c in
                    ConnectionRow(connection: c) { updated in
                        if let i = connections.firstIndex(where: { $0.source == updated.source }) { connections[i] = updated }
                    } refresh: {
                        await load()
                        model.revision += 1  // new lines may have arrived
                    }
                    Hairline()
                }
                Button(L("支付宝、微信与国内银行导入步骤")) { showingDownloads = true }
                    .buttonStyle(.keelOutlineCompact)
                if let error { Text(error).font(.ui(12)).foregroundStyle(Theme.outflow).padding(.top, 8) }
            }
        }
        .task { await load() }
        .sheet(isPresented: $showingDownloads) { DownloadGuide(initialSelection: "alipay") }
    }

    private func load() async {
        guard let api = model.api else { return }
        do {
            connections = try await api.connections()
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

private struct ConnectionRow: View {
    @Environment(AppModel.self) private var model
    let connection: AccountConnection
    let changed: (AccountConnection) -> Void
    let refresh: () async -> Void
    @State private var editing = false
    @State private var values: [String: String] = [:]
    @State private var busy: String?
    @State private var note: String?
    @State private var failed = false
    @State private var showingGuide = false
    @State private var showingPasswords = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(connection.label).font(.ui(14, .medium)).foregroundStyle(Theme.ink)
                if ["snaptrade", "ibkr_flex", "mailbox"].contains(connection.source) {
                    Button(L("连接步骤")) { showingGuide = true }.buttonStyle(.keelOutlineCompact)
                }
                Spacer()
                Text(statusText).font(.ui(12)).foregroundStyle(statusColor)
            }
            Text(help).font(.ui(12)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            if !connection.linked.isEmpty {
                Text(L("已连接：{names}", ["names": connection.linked.joined(separator: "、")]))
                    .font(.ui(12)).foregroundStyle(Theme.ink)
            }
            if let message = connection.message {
                Text(message).font(.ui(12)).foregroundStyle(connection.status == "partial" ? Theme.signal : connection.status == "ok" ? Theme.muted : Theme.outflow).fixedSize(horizontal: false, vertical: true)
            }
            if editing || !connection.connected {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(connection.fields, id: \.name) { f in
                        HStack(spacing: 10) {
                            Text(fieldLabel(f)).font(.ui(12)).foregroundStyle(Theme.ink).frame(width: 170, alignment: .leading)
                            KeelField(
                                placeholder: f.filled ? L("已保存，留空则不改") : "",
                                text: Binding(get: { values[f.name] ?? "" }, set: { values[f.name] = $0 }),
                                width: 300, mono: true, secure: f.secret
                            )
                            if connection.source == "mailbox", f.name == "password" {
                                Button(L("怎么获取？")) { showingGuide = true }.buttonStyle(.keelOutlineCompact)
                            }
                        }
                    }
                    HStack(spacing: 10) {
                        Button(L("保存并连接")) { Task { await save() } }
                            .buttonStyle(.keelPrimaryCompact).disabled(busy != nil || requiresMicrosoftOAuth)
                        if connection.connected { Button(L("取消")) { editing = false }.buttonStyle(.keelOutlineCompact) }
                        if let url = signup { Button(L("去申请") + " ↗") { NSWorkspace.shared.open(url) }.buttonStyle(.keelOutlineCompact) }
                    }
                    if requiresMicrosoftOAuth {
                        Text(L("此邮箱需要 Microsoft OAuth，当前连接方式暂不支持。点「连接步骤」查看可用方式。"))
                            .font(.ui(12)).foregroundStyle(Theme.signal)
                    }
                }
            } else {
                HStack(spacing: 10) {
                    if connection.source == "plaid" {
                        Button(L("添加银行")) { Task { await addBank() } }.buttonStyle(.keelPrimaryCompact).disabled(busy != nil)
                    }
                    if connection.source == "snaptrade" {
                        Button(L("连接券商") + " ↗") { Task { await openPortal() } }.buttonStyle(.keelPrimaryCompact).disabled(busy != nil)
                    }
                    Button(connection.source == "mailbox" ? L("检查新附件") : L("立即同步")) { Task { await sync() } }.buttonStyle(.keelOutlineCompact).disabled(busy != nil)
                    Button(L("更换密钥")) { editing = true }.buttonStyle(.keelOutlineCompact)
                    Button(L("断开")) { Task { await disconnect() } }.buttonStyle(.keelOutlineCompact).disabled(busy != nil)
                }
            }
            if connection.source == "mailbox" {
                HStack(spacing: 10) {
                    Button(L("账单解压密码")) { showingPasswords = true }.buttonStyle(.keelOutlineCompact)
                    Button(L("待处理账单")) { model.showPendingFiles = true }.buttonStyle(.keelOutlineCompact)
                }
            }
            if let busy { Text(busy).font(.ui(12)).foregroundStyle(Theme.muted) }
            if let note { Text(note).font(.ui(12)).foregroundStyle(failed ? Theme.outflow : Theme.muted).fixedSize(horizontal: false, vertical: true) }
        }
        .padding(.vertical, 16)
        .sheet(isPresented: $showingPasswords) { StatementPasswordsSheet() }
        .sheet(isPresented: $showingGuide) {
            if connection.source == "mailbox" { MailboxSetupGuide(address: values["address"] ?? "") }
            else { ConnectionSetupGuide(source: connection.source) }
        }
    }

    private var requiresMicrosoftOAuth: Bool {
        guard connection.source == "mailbox", let address = values["address"] else { return false }
        let domain = address.trimmingCharacters(in: .whitespacesAndNewlines).lowercased().split(separator: "@").last.map(String.init) ?? ""
        return ["outlook.com", "hotmail.com", "live.com", "msn.com"].contains(domain)
    }

    private func fieldLabel(_ field: ConnectionField) -> String {
        guard connection.source == "mailbox" else { return field.label }
        switch field.name {
        case "address": return L("邮箱地址")
        case "password": return L("应用专用密码 / 授权码")
        case "host": return L("IMAP 服务器（可留空）")
        default: return field.label
        }
    }

    private var statusText: String {
        guard connection.connected else { return L("未连接") }
        switch connection.status {
        case "reauth": return L("需要重新登录")
        case "error": return L("上次同步出错")
        case "partial":
            return L("{n} 个账户 · 数据未齐 · {when} 读取", ["n": String(connection.accounts), "when": syncTime])
        case "ok":
            let when = syncTime
            if connection.source == "mailbox" { return L("邮箱已连接 · {when} 检查", ["when": when]) }
            return L("{n} 个账户 · {when} 同步", ["n": String(connection.accounts), "when": when])
        default: return L("已保存，等待第一次同步")
        }
    }

    private var syncTime: String {
        guard let raw = connection.lastSync else { return "" }
        guard let date = ISO8601DateFormatter().date(from: raw) else { return String(raw.prefix(16)) }
        return DateFormatter.localizedString(from: date, dateStyle: .short, timeStyle: .short)
            + " " + (TimeZone.current.abbreviation() ?? "")
    }

    private var statusColor: Color {
        switch connection.status {
        case "reauth", "error": return Theme.outflow
        case "partial": return Theme.signal
        case "ok": return Theme.inflow
        default: return Theme.muted
        }
    }

    private var help: String {
        switch connection.source {
        case "plaid": return L("Chase、Amex、Bank of America 等银行和信用卡。保存密钥后点「添加银行」完成授权；消费正式入账后计入收支，pending 暂不计入，无需等信用卡还款。")
        case "simplefin": return L("美国的银行和信用卡，不需要开发者密钥。在 SimpleFIN Bridge 订阅并连上银行，新建一个 Setup Token 粘贴到这里即可；一个 Token 只能用一次，Keel 只换取只读访问。同一家银行只用一种方式连接（Plaid 或 SimpleFIN），不然流水会重复。")
        case "snaptrade": return L("Fidelity、Schwab、IBKR 等券商。授权成功与数据到齐是两回事；IBKR 数据按日提供，首次启用需等待。这里读取账户总值，包含已返回的现金和持仓价值。")
        case "ibkr_flex": return L("IBKR 官方 Flex Web Service：自建 Activity Flex Query，必须包含 Cash Transactions 和 NAV in Base。已通过 SnapTrade 接入同一账户时，通常无需再连接这一项。")
        case "okx": return L("OKX 只读 API Key：只勾「读取」，不要勾交易和提币。")
        case "mailbox": return L("收取账单附件和微信支付的官方下载链接。先在原 App 申请导出。Keel 只读收件箱，按平台保存解压密码，密码匹配时自动解锁并导入。")
        default: return ""
        }
    }

    private var signup: URL? {
        switch connection.source {
        case "plaid": return URL(string: "https://dashboard.plaid.com/signup")
        case "simplefin": return URL(string: "https://bridge.simplefin.org/simplefin/create")
        case "snaptrade": return URL(string: "https://snaptrade.com/personal")
        case "ibkr_flex": return URL(string: "https://www.interactivebrokers.com/portal/")
        case "okx": return URL(string: "https://www.okx.com/account/my-api")
        default: return nil
        }
    }

    private func save() async {
        guard let api = model.api else { return }
        busy = L("正在保存…")
        defer { busy = nil }
        do {
            let updated = try await api.saveConnection(connection.source, values.filter { !$0.value.isEmpty })
            values = [:]
            editing = false
            changed(updated)
            if connection.source != "plaid" { await sync() }  // Plaid first needs 添加银行
        } catch {
            show(error.localizedDescription, failed: true)
        }
    }

    private func sync() async {
        guard let api = model.api else { return }
        busy = L("正在同步…")
        defer { busy = nil }
        do {
            let r = try await api.syncConnection(connection.source)
            let title = connection.source == "mailbox" ? L("收到 {n} 份账单附件，新增 {new} 笔", ["n": String(r.accounts), "new": String(r.new)]) : r.partial == true ? L("部分数据已同步：{n} 个账户，新增 {new} 笔", ["n": String(r.accounts), "new": String(r.new)]) : L("同步完成：{n} 个账户，新增 {new} 笔", ["n": String(r.accounts), "new": String(r.new)])
            // The refreshed connection shows the detailed warning once, above the actions.
            show(r.ok ? title : L("同步失败"), failed: !r.ok)
        } catch {
            show(error.localizedDescription, failed: true)
        }
        await refresh()
    }

    private func disconnect() async {
        guard let api = model.api, let updated = try? await api.disconnect(connection.source) else { return }
        changed(updated)
        show(L("已断开。已经导入的记录会保留。"), failed: false)
    }

    private func openPortal() async {
        guard let api = model.api else { return }
        do {
            let link = try await api.snaptradePortal()
            if let url = URL(string: link.url) { NSWorkspace.shared.open(url) }
            show(L("在浏览器里登录券商；完成后回来点「立即同步」。"), failed: false)
        } catch {
            show(error.localizedDescription, failed: true)
        }
    }

    /// Plaid Hosted Link: open it in the browser, then check every few seconds until the bank
    /// sign-in finishes (up to 25 minutes: the link itself lasts 30), and sync right away.
    private func addBank() async {
        guard let api = model.api else { return }
        do {
            let link = try await api.plaidLink()
            guard let token = link.linkToken, let url = URL(string: link.url) else { return }
            NSWorkspace.shared.open(url)
            busy = L("在浏览器里登录银行，完成后这里会自动继续…")
            for _ in 0..<375 {
                try await Task.sleep(for: .seconds(4))
                let got = try await api.plaidLinkFinish(token)
                if !got.added.isEmpty {
                    busy = nil
                    show(L("已连接：{names}", ["names": got.added.joined(separator: "、")]), failed: false)
                    await sync()
                    return
                }
            }
            busy = nil
            show(L("没有等到银行登录完成。可以再点一次「添加银行」。"), failed: true)
        } catch {
            busy = nil
            show(error.localizedDescription, failed: true)
        }
    }

    private func show(_ text: String, failed: Bool) {
        note = text
        self.failed = failed
    }
}
