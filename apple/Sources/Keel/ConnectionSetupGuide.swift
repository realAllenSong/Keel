import AppKit
import KeelCore
import SwiftUI

/// Keep the two IBKR routes distinct: SnapTrade's Third-Party Service is not a custom Flex Query.
struct ConnectionSetupGuide: View {
    let source: String
    @Environment(\.dismiss) private var dismiss

    private var isSnapTrade: Bool { source == "snaptrade" }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(isSnapTrade ? L("SnapTrade 连接步骤") : L("IBKR Flex 连接步骤"))
                    .font(.display(25)).foregroundStyle(Theme.ink)
                Spacer()
                Button(L("完成")) { dismiss() }.buttonStyle(.keelOutlineCompact).keyboardShortcut(.cancelAction)
            }
            .padding(24)
            Hairline()
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text(isSnapTrade ? L("先完成授权，再核对账户是否返回") : L("直接连接 IBKR，作为 SnapTrade 的备选"))
                        .font(.ui(14, .medium)).foregroundStyle(Theme.ink)
                    Text(L("IBKR 有两条独立路径：SnapTrade 用 Third-Party Reports；直接 Flex 用 Flex Queries。已在 SnapTrade 显示 Active 时，通常不用再设置直接 Flex。"))
                        .font(.ui(12)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                    if !isSnapTrade {
                        Button(L("直达 Flex Queries") + " ↗") { NSWorkspace.shared.open(URL(string: "https://www.interactivebrokers.com/sso/resolver?action=RM_FLEX_QUERIES")!) }.buttonStyle(.keelPrimaryCompact)
                    }
                    ForEach(Array(steps.enumerated()), id: \.offset) { i, step in
                        HStack(alignment: .firstTextBaseline, spacing: 12) {
                            Text("\(i + 1)").font(.mono(11)).foregroundStyle(Theme.muted).frame(width: 18)
                            Text(step).font(.ui(13)).foregroundStyle(Theme.ink).lineSpacing(4)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Text(isSnapTrade
                         ? L("IBKR 首次启用通常需 24–48 小时，周末可能到 72 小时；之后按交易日提供延迟数据。Active 只表示授权有效，只有账户和总值返回后才能计入资产。超过 72 小时仍无账户，请按官方指南核对并联系 SnapTrade 支持。")
                         : L("如果提示「只有账户信息，没有交易或市值」，编辑原 Query 补上 Cash Transactions 和 NAV；只勾 Account Information 不够。令牌失效时重新生成并更新即可。"))
                        .font(.ui(12)).foregroundStyle(Theme.muted).lineSpacing(3)
                    HStack(spacing: 12) {
                        Button(L("打开官方指南") + " ↗") { NSWorkspace.shared.open(officialURL) }.buttonStyle(.keelPrimaryCompact)
                        Button(L("打开 IBKR Portal") + " ↗") { NSWorkspace.shared.open(URL(string: "https://www.interactivebrokers.com/portal/")!) }.buttonStyle(.keelOutlineCompact)
                    }
                }
                .padding(24)
            }
        }
        .frame(width: 700, height: 520)
        .background(Theme.paper)
    }

    private var steps: [String] {
        if isSnapTrade {
            return [
                L("在 SnapTrade Dashboard 的 API Keys 复制 Client ID 和 Consumer Key，填入 Keel 的 SnapTrade 一项，点「保存并连接」。密钥只填在 App 中。"),
                L("Fidelity、Schwab 等：点「连接券商」，在 SnapTrade 页面选择券商并完成授权。已有连接可直接使用。"),
                L("IBKR：登录正确的 IBKR 账户，进入 Performance & Reports → Third-Party Reports，点 Third-Party Services 的齿轮，勾选 SnapTrade 后保存。"),
                L("将 Third-Party Reports 中 SnapTrade 那一行的 Token 和 Query ID 填到 SnapTrade 的 IBKR 连接页。这组凭据与自己在 Flex Queries 创建的查询不同。已显示 Active 时先等待数据，无需反复断开重连。"),
                L("回 Keel 点「立即同步」。若提示某券商「尚未返回任何账户」，说明该券商还没有可导入的数据；其他券商的数据仍会正常导入。")
            ]
        }
        return [
            L("IBKR Client Portal → Performance & Reports → Flex Queries。在 Activity Flex Query 旁点 +，创建名为 Keel 的查询。"),
            L("添加 Account Information、Cash Transactions（Detail）、Net Asset Value (NAV) in Base、Open Positions（Summary），每个部分选择所需全部字段。NAV 是整个账户净值，包含现金与持仓。"),
            L("Delivery Configuration：Format 选 XML，Period 选 Last 365 Calendar Days，日期格式保留 yyyyMMdd。保存并记下 Query ID。"),
            L("回到 Flex Queries 页面，在 Flex Web Service Configuration 区块点右侧齿轮，打开 Enable Flex Web Service。令牌会显示在这个配置窗口里；需要新令牌时再点 Generate A New Token 并设置有效期。"),
            L("Keel → IBKR Flex：填 Token 和 Query ID，点「保存并连接」。不要填写 Third-Party Reports 里给 SnapTrade 的那组凭据。同一账户选择一个来源即可，重复来源会在导入时跳过。"),
            L("找不到入口时，点「直达 Flex Queries」，或尝试 Menu → Reporting → Flex Queries。Flex Web Service 不是菜单中的单独入口；仍没有配置区块时，按官方指南核对账户 / 用户权限。")
        ]
    }

    private var officialURL: URL {
        URL(string: isSnapTrade
            ? "https://support.snaptrade.com/End-user-guide-for-connecting-Interactive-Brokers-f58feaa69a1c836ea2138120e68b6c36"
            : "https://www.ibkrguides.com/clientportal/performanceandstatements/flex-web-service.htm")!
    }
}
