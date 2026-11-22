import KeelCore
import SwiftUI

struct StatementPasswordsSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var statuses: [StatementPasswordStatus] = []
    @State private var pending: [PendingImport] = []
    @State private var provider = "alipay"
    @State private var password = ""
    @State private var busy = false
    @State private var error: String?
    @State private var result: StatementPasswordResult?
    private var providers: [String] {
        let named = ["alipay", "wechat", "cmb", "ningbo"]
        return statuses.contains { $0.provider == "other" && $0.configured } ? named + ["other"] : named
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(L("账单解压密码")).font(.display(24)); Spacer()
                Button(L("关闭")) { dismiss() }.buttonStyle(.keelOutlineCompact).keyboardShortcut(.cancelAction)
            }.padding(24)
            Hairline()
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text(L("保存后立即重试已收到的待解锁账单；以后同步或导入加密 ZIP 时，也会自动尝试这个平台最近保存的密码。")).font(.ui(13)).fixedSize(horizontal: false, vertical: true)
                    Picker(L("账单平台"), selection: $provider) {
                        ForEach(providers, id: \.self) { Text(title($0)).tag($0) }
                    }.pickerStyle(.segmented).disabled(busy).onChange(of: provider) { _, _ in password = ""; result = nil; error = nil }
                    VStack(alignment: .leading, spacing: 8) {
                        Text(L("本次账单的解压密码")).font(.ui(12)).foregroundStyle(Theme.muted)
                        KeelField(placeholder: L("不是邮箱密码或支付密码"), text: $password, width: 300, mono: true, secure: true).disabled(busy)
                        Button(busy ? L("正在解锁…") : L("保存并重试待解锁账单")) { Task { await save() } }
                            .buttonStyle(.keelPrimaryCompact).disabled(busy || password.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                    Text(L("密码保存在本机钥匙串，不会写进账本或显示回来；每个平台保留最多 5 个近期密码。每次导出可能换码，旧码不匹配时会继续等待你补填，不会丢弃文件。")).font(.ui(12)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                    if let result {
                        Text(L("已解锁 {n} 份，新增 {t} 笔交易；仍有 {r} 份需要密码。", ["n": result.unlocked, "t": result.newTransactions, "r": result.remaining])).font(.ui(13, .medium))
                        if result.remaining > 0 { Text(L("没有解锁通常是密码属于另一份账单，请核对对应本次导出的消息。")).font(.ui(12)).foregroundStyle(Theme.muted) }
                        if result.errors > 0 { Text(L("有文件未能处理，请从邮箱旁的「待处理账单」查看；文件仍保留。")).font(.ui(12)).foregroundStyle(Theme.outflow) }
                    }
                    if let error { ErrorLine(message: error) }
                    Hairline()
                    ForEach(statuses.filter { providers.contains($0.provider) }) { status in
                        HStack {
                            Text(title(status.provider)).font(.ui(13)); Spacer()
                            Text(status.configured ? L("已保存 {n} 个近期密码", ["n": status.count]) : L("未保存")).font(.ui(12)).foregroundStyle(Theme.muted)
                            if status.configured {
                                Button(L("清除已保存密码")) { Task { await clear(status.provider) } }.buttonStyle(.keelOutlineCompact).disabled(busy)
                            }
                        }
                    }
                    if !pending.isEmpty {
                        Hairline()
                        Text(L("待解锁账单")).font(.ui(14, .medium))
                        ForEach(pending) { item in Text(item.filename).font(.ui(12)).foregroundStyle(Theme.muted) }
                    }
                    Text(L("邮箱收取账单附件，也可以下载微信支付发来的官方账单链接。每次仍在原 App 申请账单。")).font(.ui(12)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                }.padding(24)
            }
        }.frame(width: 740, height: 650).background(Theme.card).task { await load() }
    }

    private func title(_ provider: String) -> String {
        switch provider {
        case "alipay": return L("支付宝")
        case "wechat": return L("微信支付")
        case "cmb": return L("招商银行")
        case "ningbo": return L("宁波银行")
        default: return L("其他银行账单")
        }
    }
    private func load() async {
        guard let api = model.api else { return }
        do { statuses = try await api.statementPasswords(); pending = try await api.pending().filter { $0.reason == "needs_password" } }
        catch { self.error = error.localizedDescription }
    }
    private func save() async {
        guard let api = model.api else { return }
        let selected = provider
        let code = password
        busy = true; error = nil
        defer { busy = false }
        do {
            let response = try await api.saveStatementPassword(selected, password: code)
            password = ""; result = response; statuses = response.passwords; model.revision += 1
            pending = try await api.pending().filter { $0.reason == "needs_password" }
        } catch { self.error = error.localizedDescription }
    }
    private func clear(_ provider: String) async {
        guard let api = model.api else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            statuses = try await api.clearStatementPasswords(provider)
            if !providers.contains(self.provider) { self.provider = "alipay" }
        } catch { self.error = error.localizedDescription }
    }
}
