# 连接账户与同步：按实际数据状态检查

在 **Keel → 设置 → 连接账户** 查看状态。密钥只填在 App 中，由这台 Mac 的钥匙串保存。
Keel 的连接器只调用读取接口；OKX 创建密钥时只勾「读取」，券商授权选择只读。

## 自动同步究竟什么时候发生

- **Keel 运行期间，每天本机时间 07:30 之后自动尝试一次。** 调度器每 30 秒检查是否到时间，并不是每 30 秒拉取账户。
- 如果当天尚未执行，07:30 以后启动 App 会补一次。关闭窗口后菜单栏仍在运行；退出 Keel 后内置任务停止。Mac 休眠期间不执行，恢复后检查。
- 每日任务先更新行情，再依次读取已连接的 Plaid、SnapTrade、IBKR Flex、OKX、邮箱，因此不会全部在 07:30 整同时完成。
- 当天失败不会自动重试；可点对应来源的 **立即同步**。保存新连接也会同步一次；Plaid 需先完成「添加银行」。
- 设置页显示的是 **Keel 读取数据的时间**，不是银行刚刚更新数据的保证。手动同步读取上游当时提供的数据。
- SnapTrade 的 IBKR 数据按交易日延迟提供；「立即同步」不会让 IBKR 提前生成报表。Keel 不调用可能收费的 SnapTrade 强制刷新接口。
- 邮箱每天检查已收到的附件及微信支付的官方账单下载链接。

## 已连接，但为什么看不到 IBKR 的钱

**授权有效（Active）≠ 账户数据已返回。** SnapTrade 的首页 Portfolio Value 是所有已返回账户的合计，
不是 IBKR 单独的余额。若占比里只有 Fidelity、Schwab，就不能据此判断 IBKR 只读了现金。
Keel 读取 SnapTrade 的账户 **total account value**，而不是拿现金余额代替整个账户价值。

设置页会区分：

| 状态 | 含义与下一步 |
| --- | --- |
| 正常同步 | 当前返回的数据已导入；仍受上游更新时间限制 |
| 数据未齐 | 其他账户可导入，但某券商无账户、首次同步未完成，或持仓明细未取到；查看具体提示 |
| 需要重新登录 / 授权失效 | 回到相应服务修复授权 |
| 同步出错 | 查看错误后重试，不要先反复删除连接 |

SnapTrade 官方说明：IBKR 首次启用通常需 **24–48 小时**，周末可能到 **72 小时**；后续数据最多延迟一个工作日，节假日另有延迟。
若超过 72 小时仍没有账户，核对正确的 IBKR 账户、Third-Party Services 中的 SnapTrade 服务及凭据有效期，再联系 SnapTrade 支持。
参考：[SnapTrade IBKR 连接指南](https://support.snaptrade.com/End-user-guide-for-connecting-Interactive-Brokers-f58feaa69a1c836ea2138120e68b6c36)、
[IBKR 数据支持及延迟](https://support.snaptrade.com/Interactive-Brokers-f62feaa69a1c822ebb0e01a28b2f195a)。

## 路径一：通过 SnapTrade 连接券商

1. 登录 [SnapTrade Dashboard](https://dashboard.snaptrade.com/)，在 **API Keys** 复制 Client ID、Consumer Key。
2. 填入 Keel 的 **SnapTrade → 保存并连接**。已有 Personal 账户使用现有连接，无需另外注册商业应用。
3. 对 Fidelity、Schwab 等，点 **连接券商**，在浏览器完成该券商要求的授权。可用机构及限制以自己的 Dashboard 为准。
4. 对 IBKR：登录正确的 [Client Portal](https://www.interactivebrokers.com/portal/)，打开 **Performance & Reports → Third-Party Reports**。
5. 点 **Third-Party Services** 的齿轮，勾选 **SnapTrade**，保存。
6. 将该页 **SnapTrade 那一行**显示的 **Token、Query ID** 填到 **SnapTrade 的 IBKR 连接页面**。
7. 返回 Keel 点 **立即同步**，核对是否已有 IBKR 账户、总值，再与 IBKR 官方账户总值比较。

这里使用的是 **Third-Party Reports 的 SnapTrade 服务凭据**，不同于下面自己建的 Activity Flex Query。
已经 Active 但数据还没到时，先按上面的初次等待时间判断。
官方：[SnapTrade Personal 设置](https://snaptrade.com/personal-account-setup)、[IBKR Third-Party Reports](https://www.ibkrguides.com/clientportal/performanceandstatements/third-party-reports.htm)。

## 路径二：直接使用 IBKR Flex（SnapTrade 的备选）

已通过 SnapTrade 连接同一个 IBKR 账户时，通常不需要再开 Flex。同一账户以第一个实际带回交易或市值的来源为准，
重复来源会跳过并说明；只含账户信息的空 Query 不占用来源。断开连接会保留已经导入的历史数据。

1. Client Portal → **Performance & Reports → Flex Queries**，在 **Activity Flex Query** 旁点 **+**，命名 `Keel`。
2. 添加并选中各部分所需全部字段：**Account Information**、**Cash Transactions（Detail）**、**Net Asset Value (NAV) in Base**、**Open Positions（Summary）**。
3. **Delivery Configuration**：Format 选 **XML**，Period 选 **Last 365 Calendar Days**，日期格式保留 `yyyyMMdd`，保存。
4. 复制该自建查询的 **Query ID**。
5. 回到 **Flex Queries** 页面，找到 **Flex Web Service Configuration 区块**，点右侧齿轮并打开 **Enable Flex Web Service**；Token 在配置窗口中显示。只有需要替换时再点 **Generate A New Token**，它会使旧 Token 失效。
6. 填入 Keel 的 **IBKR Flex**，点 **保存并连接**；不要填 Third-Party Reports 中给 SnapTrade 的另一组凭据。
7. 若提示「只有账户信息，没有交易或市值」，编辑原 Query 补上 Cash Transactions 和 NAV，再同步；无需先换密钥。

NAV 是整个账户净值，包含现金与持仓。Token 失效后需重新生成并更新。
官方：[IBKR Flex Queries](https://www.ibkrguides.com/clientportal/performanceandstatements/flex.htm)。

**找不到 Flex Web Service？** 它是 Flex Queries 页面里的配置区块，不是导航菜单中的独立入口。
另一条菜单路径是 **Menu → Reporting → Flex Queries**；可用 [IBKR 官方直达入口](https://www.interactivebrokers.com/sso/resolver?action=RM_FLEX_QUERIES)。
若仍没有配置区块，核对当前登录用户的报表权限与账户类型，见 [官方启用指南](https://www.ibkrguides.com/clientportal/performanceandstatements/flex-web-service.htm)。
已经通过 SnapTrade 的 Third-Party Reports 接入 IBKR 时，通常不用再设置直接 Flex。

## 国内平台：支付宝、微信支付、招商银行、宁波银行

目前这四个平台在 Keel 中走 **账单导入**，不是持续自动登录原平台抓取交易。
App 内入口：**设置 → 连接账户 → 支付宝、微信与国内银行导入步骤**，或原有的 **怎么下载账单**。
下面菜单需要以自己的 App 版本为准；尚未逐个在你的手机上验证。

| 平台 | 当前能做什么 | 限制 |
| --- | --- | --- |
| 支付宝 | 解析个人对账 CSV / 加密 ZIP |
| 微信支付 | 下载邮件中的官方账单链接，解析个人对账 CSV、Excel / 加密 ZIP |
| 宁波银行 | 解析带日期、金额、余额的特定 CSV / Excel 布局 | 尚未核对你的实际导出；不支持 PDF |
| 招商银行 | 导入已核对过的单币种交易流水 PDF / 加密 ZIP，并校验逐笔余额 | 多币种流水或其他 PDF 格式需要单独核对 |

**支付宝**

1. 我的 → 账单 → 右上角「···」→ 开具交易流水证明，用途选 **用于个人对账**。
2. 选择时间范围、接收邮箱，完成身份验证。优先导出最近一个月，核对成功再补历史。
3. 将邮件中的 ZIP 下载到电脑，直接拖进 Keel；解压密码在支付宝 App 的相应消息中，填入 App 提示框。

**微信支付**

1. 我 → 服务 → 钱包 → 账单 → 常见问题 → 下载账单，用途选 **用于个人对账**。
2. 在整页账单中找「常见问题」→「下载账单」，不是打开单笔交易；范围按当前 App 提示分批导出。入口不见时：钱包→消费者保护→帮助中心，搜索「下载账单」。
3. 将邮件发到 Keel 已连接的邮箱，或下载 ZIP 后拖进 Keel。使用「微信支付」消息中对应本次账单的解压密码。

**招商银行**

1. 招商银行 App 搜索 **流水打印**，选择一卡通、范围和邮箱；信用卡请在「掌上生活」查看相应账单。
2. Keel 支持招行「交易流水」PDF，邮件中的加密 ZIP 也可直接导入。
3. 在招行 App 的「流水打印 → 申请记录」取对应这次申请的解压密码，填入 Keel 的「招商银行」。

**宁波银行**

1. 在 App 或个人网银选择银行卡，打开交易明细，查找 **导出 / 下载 / 流水打印**；入口不符时在 App 内客服查询「导出交易明细」。
2. 先导出一个月 **CSV / Excel**，需包含日期、金额、余额。支持收入 / 支出两列，或交易金额加借贷标志。
3. 文件名保留「宁波银行」，拖进 Keel 核对币种、收支方向和期末余额，再补后续月份。若仅有 PDF，当前不能直接导入。

同一笔银行卡付款可能同时出现在银行和支付平台账单中，当前不能保证自动跨平台去重，先核对同笔扣款再批量补历史。
支付宝、微信账单只用于收支，不用于推算零钱、余额宝或银行卡资产；银行余额应单独核对。
招行流水和支持的宁波银行表格会同时更新对应账户的已核验余额。招行取文件的截止日期，宁波银行取可核对的最后一笔余额日期。银行总资产中的理财、基金等部分仍需单独的持仓明细或总资产记录。

### 邮箱能自动做哪一段

邮箱连接收取已经发来的账单附件，以及微信支付发来的官方账单下载链接。每次新账期仍需在原 App 申请。
App 内的 **Mailbox → 连接步骤**、密码框旁的 **怎么获取？** 都会打开按邮箱服务商区分的弹窗。
这里不需要申请邮件 API，所需的是邮箱服务商提供的应用专用密码 / 客户端授权码。

1. 在邮箱提供商开启 IMAP，生成应用专用密码 / 授权码（不使用网页登录密码）。例如 [Gmail 应用密码](https://myaccount.google.com/apppasswords)，或 QQ / 163 的 IMAP 授权码。
2. Keel → **Mailbox** 填邮箱、应用专用密码；常见邮箱自动识别服务器，其他邮箱再填 IMAP 主机。
3. 保存后读取一次，此后跟随每日同步；要马上收附件可点 **立即同步**。连接以只读方式打开收件箱，不删信、不标已读。
4. 对加密 ZIP，在设置→连接账户→Mailbox旁的「账单解压密码」选择支付宝、微信支付、招商银行或宁波银行。保存后立即重试；后续同步会尝试同平台最多 5 个已保存密码。

**在哪里获取邮箱密码：**

- Gmail：先开启 Google 账号两步验证，再打开 [应用密码页面](https://myaccount.google.com/apppasswords)，名称填 Keel，创建后将 16 位密码填入 App，不带分组空格。工作 / 学校、仅安全密钥验证或高级保护账号可能没有入口，见 [Google 官方帮助](https://support.google.com/accounts/answer/185833)。
- QQ：电脑登录 [QQ 邮箱](https://mail.qq.com/)，右上角头像 → 设置 → 账号与安全 → 安全设置，开启 POP3/IMAP/SMTP，再生成授权码；填授权码，不填 QQ 密码。见 [QQ 官方帮助](https://help.mail.qq.com/detail/106/985)。
- 163 / 126：登录对应网页邮箱，设置 → POP3/SMTP/IMAP，开启 IMAP，再在授权码管理中新增客户端授权码；菜单以实际版本为准。
- iCloud：确认双重认证和 iCloud 邮箱已开通，登录 [Apple 账号](https://account.apple.com/) → 登录与安全 → App 专用密码，生成给 Keel 使用的密码，见 [Apple 官方帮助](https://support.apple.com/102654)。
- Outlook / Hotmail：服务商要求 OAuth；Keel 当前尚未实现 Microsoft OAuth，不能靠更换应用密码接入。先下载账单拖入，或使用支持的收件邮箱，见 [Microsoft 官方设置](https://support.microsoft.com/en-us/outlook/pop-imap-and-smtp-settings-for-outlook-com)。

邮箱地址填写完整的收信地址。IMAP 服务器常见服务商可留空自动识别，其他服务商填官方说明里的主机名。
首次扫描收件箱最近 45 天内最多 300 封邮件。0 份文件表示这次没有收到可导入的账单。
邮箱应用密码、普通登录密码、支付宝 / 微信 ZIP 解压密码是不同凭据，分别在对应提示框使用。

## 现有 Plaid 与 OKX 连接

Plaid 已连接时点 **添加银行** 才是增加银行授权；**立即同步** 读取现有银行的新数据。
只把已经入账的交易计入正式收支，`pending` 交易目前会跳过，所以今天刚刷的卡可能暂时看不到；
**不是等你还信用卡才算消费**。Plaid 后续返回正式入账交易时会导入。

新建 Plaid 连接：在 [Plaid Dashboard](https://dashboard.plaid.com/) 按自己的计划开通真实机构权限，
把 client_id 和 Production secret 填进 Keel，保存后添加银行并完成浏览器授权。费用和机构可用性以自己的 Dashboard 为准。

OKX：创建 V5 只读 Key，填 API Key、Secret Key、Passphrase；美国站区域填 `us`，欧洲站填 `eea`，其他留空。
已有连接正常时无需重建。同步会读取账户估值及连接器支持的资金流水，交易买卖不当作日常消费重复记账。

历史文件仍可拖入 Keel；同一来源重复导入会去重。API 和历史文件的衔接受账户匹配与覆盖期规则控制，
不要假设不同平台的全部重复记录都能靠文件哈希自动识别。

密码保存在本机钥匙串，不返回界面、不写入账本。待处理文件的单次解压也可勾选记住密码；不勾选则只用一次。邮箱应用密码、支付密码、账单解压码相互独立。官方Agent支付工具与消费者历史流水读取是不同能力，调研见 `china-data-access-research-2026-11-02.md`。
