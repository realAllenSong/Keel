import AppKit
import KeelCore
import SwiftUI

/// Explain the credential at the field where it is needed, without asking for it in a browser.
struct MailboxSetupGuide: View {
    var address = ""
    @Environment(\.dismiss) private var dismiss
    @State private var provider = MailProvider.gmail

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 6) {
                    Text(L("连接邮箱，自动收账单")).font(.display(25)).foregroundStyle(Theme.ink)
                    Text(provider == .outlook ? L("此邮箱需要 Microsoft OAuth，当前尚不支持；可以先下载账单拖入 Keel。") : L("不需要申请邮件 API；这里填写邮箱服务商生成的「应用专用密码 / 授权码」。"))
                        .font(.ui(12)).foregroundStyle(Theme.muted)
                }
                Spacer()
                Button(L("完成")) { dismiss() }.buttonStyle(.keelOutlineCompact).keyboardShortcut(.cancelAction)
            }.padding(24)
            Hairline()
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Picker(L("你使用的邮箱"), selection: $provider) {
                        ForEach(MailProvider.allCases, id: \.self) { Text($0.title).tag($0) }
                    }.pickerStyle(.segmented)
                    VStack(alignment: .leading, spacing: 12) {
                        Text(provider == .outlook ? L("当前连接方式不适用于 Outlook") : L("先在邮箱服务商处生成密码")).font(.ui(14, .medium)).foregroundStyle(Theme.ink)
                        ForEach(Array(provider.steps.enumerated()), id: \.offset) { i, step in
                            HStack(alignment: .firstTextBaseline, spacing: 10) {
                                Text("\(i + 1)").font(.mono(11)).foregroundStyle(Theme.muted).frame(width: 18)
                                Text(step).font(.ui(13)).foregroundStyle(Theme.ink).fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        HStack(spacing: 10) {
                            if let url = provider.settingsURL {
                                Button(provider == .outlook ? L("查看 Microsoft 支持说明") + " ↗" : L("打开密码设置入口") + " ↗") { NSWorkspace.shared.open(url) }
                                    .buttonStyle(.keelPrimaryCompact)
                            }
                            if let url = provider.helpURL {
                                Button(L("打开官方指南") + " ↗") { NSWorkspace.shared.open(url) }.buttonStyle(.keelOutlineCompact)
                            }
                        }
                        Text(provider.troubleshooting).font(.ui(12)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                    }
                    if provider != .outlook {
                        Hairline()
                        VStack(alignment: .leading, spacing: 10) {
                            Text(L("再回到 Keel 填这三项")).font(.ui(14, .medium)).foregroundStyle(Theme.ink)
                            field(L("邮箱地址"), L("填写接收账单的完整地址，例如 name@gmail.com；不是只填用户名。"))
                            field(L("应用专用密码 / 授权码"), L("粘贴刚生成的那一串；不是邮箱登录密码，也不是支付宝 / 微信 ZIP 的解压密码。"))
                            field(L("IMAP 服务器（可留空）"), provider.host.isEmpty ? L("其他邮箱按服务商的客户端设置填写 IMAP 主机名，使用 SSL 连接。") : L("这一类会自动识别为 {host}，通常留空即可。", ["host": provider.host]))
                        }
                        Hairline()
                        VStack(alignment: .leading, spacing: 8) {
                            Text(L("保存后会发生什么")).font(.ui(14, .medium)).foregroundStyle(Theme.ink)
                            Text(L("点「保存并连接」会立即检查收件箱。首次查看最近 45 天内最多 300 封邮件，只下载可识别的 ZIP、CSV、Excel 账单附件；不删除邮件，也不标为已读。"))
                            Text(L("连接成功但收到 0 份文件，通常表示没有新的可导入账单。先在原 App 导出并发到这个邮箱，再点「检查新附件」。新解压码可在「账单解压密码」里按平台保存。"))
                            Text(L("Keel 保持运行时每天本机时间 07:30 后自动检查一次；晚于该时间启动会补一次。每日失败不自动重试，可手动检查。它不会替你在原 App 申请新账单。"))
                        }.font(.ui(12)).foregroundStyle(Theme.muted)
                    }
                }.lineSpacing(3).padding(24)
            }
        }
        .frame(width: 760, height: 660).background(Theme.paper)
        .onAppear { provider = MailProvider.detect(address) }
    }

    private func field(_ title: String, _ description: String) -> some View {
        HStack(alignment: .top, spacing: 14) {
            Text(title).font(.ui(12, .medium)).foregroundStyle(Theme.ink).frame(width: 178, alignment: .leading)
            Text(description).font(.ui(12)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
        }
    }
}

private enum MailProvider: String, CaseIterable {
    case gmail, qq, netease, icloud, outlook, other

    static func detect(_ address: String) -> Self {
        let domain = address.trimmingCharacters(in: .whitespacesAndNewlines).lowercased().split(separator: "@").last.map(String.init) ?? ""
        switch domain {
        case "gmail.com", "": return .gmail
        case "qq.com": return .qq
        case "163.com", "126.com": return .netease
        case "icloud.com", "me.com": return .icloud
        case "outlook.com", "hotmail.com", "live.com", "msn.com": return .outlook
        default: return .other
        }
    }

    var title: String {
        switch self {
        case .gmail: return "Gmail"
        case .qq: return L("QQ 邮箱")
        case .netease: return "163 / 126"
        case .icloud: return "iCloud"
        case .outlook: return "Outlook"
        case .other: return L("其他邮箱")
        }
    }

    var steps: [String] {
        switch self {
        case .gmail: return [
            L("登录接收账单的 Google 账号，打开「安全性」→「两步验证」，先确认已开启。"),
            L("点下方入口直达 myaccount.google.com/apppasswords，应用名称填 Keel，点击创建。"),
            L("复制生成的 16 位应用专用密码，粘贴到 Keel 的密码框，不带分组空格。")]
        case .qq: return [
            L("在电脑浏览器登录 mail.qq.com，点击右上角头像 →「设置」→「账号与安全」→「安全设置」。"),
            L("开启 POP3/IMAP/SMTP 服务，再点「生成授权码」，按页面完成身份验证。"),
            L("把生成的授权码填到 Keel；不要填写 QQ 密码或邮箱独立密码。")]
        case .netease: return [
            L("在电脑登录 mail.163.com（126 邮箱使用 mail.126.com），进入「设置」→「POP3/SMTP/IMAP」。"),
            L("开启 IMAP/SMTP 服务，找到「新增授权密码 / 授权码管理」，按提示验证并新增一个给 Keel 使用。"),
            L("复制新生成的客户端授权码，回 Keel 填写；菜单名称以当前邮箱版本为准。")]
        case .icloud: return [
            L("先确认 Apple 账号开启了双重认证，并已开通 iCloud 邮箱。"),
            L("登录 account.apple.com →「登录与安全」→「App 专用密码」→「生成 App 专用密码」，名称填 Keel。"),
            L("将新生成的密码填到 Keel；邮箱地址填写实际收信的 iCloud / me.com 地址。")]
        case .outlook: return [
            L("Outlook / Hotmail 现在要求 OAuth 登录，Keel 当前邮箱连接只支持 IMAP 应用专用密码方式，尚未实现 Microsoft OAuth。"),
            L("这类邮箱当前不能靠普通密码或应用密码完成连接。可先下载账单拖入 Keel，或把账单发送到已支持的邮箱。")]
        case .other: return [
            L("打开邮箱服务商的官方帮助，查找「IMAP 客户端设置」与「应用专用密码 / 客户端授权码」。"),
            L("若服务商支持，开启 IMAP 并生成给 Keel 的独立密码，同时记下 IMAP 主机名；企业邮箱可能需要管理员开放。"),
            L("若只提供 OAuth 网页授权，当前连接方式暂不支持；先下载账单拖入 Keel。")]
        }
    }

    var troubleshooting: String {
        switch self {
        case .gmail: return L("找不到应用密码入口：先检查两步验证。工作 / 学校账号、仅安全密钥验证或高级保护账号可能不提供这个入口；不要改填登录密码。")
        case .qq, .netease: return L("验证成功却登录失败：检查 IMAP 服务是否开启、授权码是否过期或复制有误；更改账号密码后可能需要重新生成授权码。")
        case .icloud: return L("找不到 App 专用密码时，先检查双重认证；更改 Apple 账号主密码后，需要重新生成 App 专用密码。")
        case .outlook: return L("不要反复更换密码尝试；这属于当前连接方式的支持限制。")
        case .other: return L("服务器一栏填写主机名，不填网页登录网址；不要猜服务器地址。")
        }
    }

    var host: String {
        switch self {
        case .gmail: return "imap.gmail.com"
        case .qq: return "imap.qq.com"
        case .netease: return "imap.163.com / imap.126.com"
        case .icloud: return "imap.mail.me.com"
        default: return ""
        }
    }

    var settingsURL: URL? {
        let value: String
        switch self {
        case .gmail: value = "https://myaccount.google.com/apppasswords"
        case .qq: value = "https://mail.qq.com/"
        case .netease: value = "https://mail.163.com/"
        case .icloud: value = "https://account.apple.com/"
        case .outlook: value = "https://support.microsoft.com/en-us/outlook/pop-imap-and-smtp-settings-for-outlook-com"
        case .other: return nil
        }
        return URL(string: value)
    }

    var helpURL: URL? {
        switch self {
        case .gmail: return URL(string: "https://support.google.com/accounts/answer/185833")
        case .qq: return URL(string: "https://help.mail.qq.com/detail/106/985")
        case .icloud: return URL(string: "https://support.apple.com/102654")
        default: return nil
        }
    }
}
