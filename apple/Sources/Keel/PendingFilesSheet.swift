import KeelCore
import SwiftUI

/// 等你确认的账单: statements Keel could not book on its own. Tell it which account a file belongs
/// to, the password of a 支付宝 / 微信 zip, or what each column of an unknown layout means; each is
/// taught once.
struct PendingFilesSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var items: [PendingImport]?
    @State private var error: String?
    @State private var notice: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(L("等你确认的账单")).font(.display(26)).foregroundStyle(Theme.ink)
                    Text(L("告诉应用这是哪个账户，或者每一列是什么意思——只需要教一次。")).font(.ui(13)).foregroundStyle(Theme.muted)
                }
                Spacer()
                Button(L("完成")) { dismiss() }.buttonStyle(.keelOutlineCompact).keyboardShortcut(.cancelAction)
            }
            .padding(24)
            Hairline()
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if let notice { Text(notice).font(.ui(13)).foregroundStyle(Theme.inflow) }
                    if let error {
                        ErrorLine(message: error)
                    } else if let items {
                        if items.isEmpty {
                            Text("✓ " + L("没有待处理文件，可以继续。")).font(.ui(13)).foregroundStyle(Theme.muted).padding(.vertical, 8)
                        } else {
                            AccountGroups(items: items.filter { $0.reason == "needs_account" }, done: done)
                            ForEach(items.filter { $0.reason != "needs_account" }) { item in
                                PendingFileView(item: item, done: done)
                                Hairline()
                            }
                        }
                    } else {
                        Text(L("加载待处理文件…")).font(.ui(13)).foregroundStyle(Theme.muted)
                    }
                }
                .padding(24)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(width: 780, height: 600)
        .background(Theme.paper)
        .task { await reload() }
    }

    private func reload() async {
        guard let api = model.api else { return }
        do {
            items = try await api.pending()
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        model.pendingFileCount = items?.count ?? 0
    }

    private func done(_ message: String) {
        notice = message
        model.revision += 1
        Task {
            await reload()
            await model.refreshBadge()
        }
    }
}

/// Seventy statements are usually three accounts: one row per account, not one per file.
private struct AccountGroups: View {
    @Environment(AppModel.self) private var model
    let items: [PendingImport]
    let done: (String) -> Void
    @State private var renaming: Set<String> = []
    @State private var names: [String: String] = [:]
    @State private var working = false
    @State private var error: String?

    struct Group: Identifiable {
        let id: String
        let first: PendingImport
        var files: [PendingImport]
    }

    private var groups: [Group] {
        var out: [Group] = []
        for item in items {
            let key = "\(item.importer ?? "")|\(item.hint ?? "")"
            if let i = out.firstIndex(where: { $0.id == key }) { out[i].files.append(item) } else { out.append(Group(id: key, first: item, files: [item])) }
        }
        return out
    }

    var body: some View {
        let groups = groups
        if !groups.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text(L("发现 {n} 个新账户", ["n": groups.count])).font(.display(19)).foregroundStyle(Theme.ink)
                    Spacer()
                    if groups.count > 1 {
                        Button(working ? L("正在入账…") : L("全部按建议确认")) { Task { await claim(groups) } }
                            .buttonStyle(.keelPrimaryCompact).disabled(working)
                    }
                }
                VStack(spacing: 0) {
                    ForEach(Array(groups.enumerated()), id: \.element.id) { i, g in
                        if i > 0 { Hairline() }
                        row(g)
                    }
                }
                .background(Theme.card, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(Theme.border))
                if let error { ErrorLine(message: error) }
            }
        }
    }

    private func row(_ g: Group) -> some View {
        let dates = g.files.map(\.filename).sorted()
        let kind = g.first.accountType == "Liabilities" ? L("信用卡 / 负债") : L("银行 / 资产")
        let range = g.files.count > 1 ? " · \(dates.first ?? "") … \(dates.last ?? "")" : " · \(dates.first ?? "")"
        return HStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 3) {
                (Text(g.first.institution ?? L("未知机构")).font(.ui(13.5, .medium)).foregroundColor(Theme.ink)
                    + Text(g.first.hint.map { " · " + L("尾号 {h}", ["h": $0]) } ?? "").font(.mono(12)).foregroundColor(Theme.muted))
                Text(kind + " · " + L("{n} 份账单", ["n": g.files.count]) + range)
                    .font(.ui(11.5)).foregroundStyle(Theme.muted).lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            // The account in words ("Chase › 支票账户 0001"); the ledger name only when you rename it.
            if renaming.contains(g.id) {
                KeelField(
                    placeholder: L("例如 Assets:银行:Checking"),
                    text: Binding(get: { names[g.id] ?? g.first.suggestedAccount ?? "" }, set: { names[g.id] = $0 }),
                    width: 260, mono: true
                )
            } else {
                VStack(alignment: .trailing, spacing: 2) {
                    Text(Format.account(names[g.id] ?? g.first.suggestedAccount)).font(.ui(13)).foregroundStyle(Theme.ink)
                    Button(L("改名")) { renaming.insert(g.id) }
                        .buttonStyle(.plain).font(.ui(11.5)).foregroundStyle(Theme.muted).pointingHandCursor()
                }
                .frame(width: 260, alignment: .trailing)
            }
            Button(L("确认")) { Task { await claim([g]) } }.buttonStyle(.keelOutlineCompact).disabled(working)
            Button(L("忽略")) { Task { await ignore(g) } }.buttonStyle(.plain).font(.ui(12)).foregroundStyle(Theme.muted).disabled(working)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }

    private func claim(_ targets: [Group]) async {
        guard let api = model.api else { return }
        working = true
        defer { working = false }
        do {
            for g in targets {
                let account = (names[g.id] ?? g.first.suggestedAccount ?? "").trimmingCharacters(in: .whitespaces)
                if account.isEmpty { throw APIError(status: 0, message: L("请先填写账户名称")) }
                _ = try await api.claim(g.first.sha256, account: account)
            }
            error = nil
            done(L("已入账 {n} 份账单，以后同一账户会自动导入。", ["n": targets.reduce(0) { $0 + $1.files.count }]))
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func ignore(_ g: Group) async {
        guard let api = model.api else { return }
        working = true
        defer { working = false }
        do {
            for f in g.files { try await api.ignore(f.sha256) }
            done(L("已忽略 {a} 的 {n} 份文件", ["a": g.first.institution ?? L("这个账户"), "n": g.files.count]))
        } catch {
            self.error = error.localizedDescription
        }
    }
}

private struct PendingFileView: View {
    @Environment(AppModel.self) private var model
    let item: PendingImport
    let done: (String) -> Void
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(item.filename).font(.display(18)).foregroundStyle(Theme.ink).textSelection(.enabled)
                    Text(subtitle).font(.ui(11.5)).foregroundStyle(Theme.muted)
                }
                Spacer()
                Button(L("忽略")) {
                    Task {
                        guard let api = model.api else { return }
                        do {
                            try await api.ignore(item.sha256)
                            done(L("已忽略 {f}", ["f": item.filename]))
                        } catch {
                            self.error = error.localizedDescription
                        }
                    }
                }
                .buttonStyle(.plain).font(.ui(12)).foregroundStyle(Theme.muted)
            }
            if let error { ErrorLine(message: error) }
            if item.reason == "needs_password" {
                UnlockForm(item: item, finish: finish)
            } else {
                TeachForm(item: item, finish: finish)
            }
        }
        .padding(.vertical, 8)
    }

    private var subtitle: String {
        if item.reason == "needs_password" {
            let app = switch item.institution {
            case "Alipay": L("支付宝")
            case "WeChat": L("微信支付")
            case "China Merchants Bank": L("招商银行")
            case "Bank of Ningbo": L("宁波银行")
            default: ""
            }
            return L("{a}账单压缩包 · 输入解压密码就能入账", ["a": app])
        }
        return L("尚未识别的格式 · 选择列映射，教一次就能记住")
    }

    private func finish(_ r: ImportResult) {
        if r.status == "error" {
            error = r.message ?? L("导入失败")
            return
        }
        let imported = r.status == "imported" || r.status == "already_imported"
        done(imported
            ? L("已导入 {f}", ["f": item.filename]) + (r.message.map { " · " + $0 } ?? "")
            : L("已学会 {f} 的格式，请确认账户。", ["f": item.filename]))
    }
}

/// 支付宝 / 微信 mail their bills as encrypted zips; the code arrives in the app, not the email.
private struct UnlockForm: View {
    @Environment(AppModel.self) private var model
    let item: PendingImport
    let finish: (ImportResult) -> Void
    @State private var password = ""
    @State private var remember = true
    @State private var working = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .bottom, spacing: 12) {
                Field(label: L("解压密码")) { KeelField(placeholder: L("6 位数字"), text: $password, width: 160, mono: true, secure: true) }
                    .frame(width: 160)
                Button(working ? L("正在解压…") : L("解压并入账")) {
                    Task {
                        guard let api = model.api else { return }
                        working = true
                        defer { working = false }
                        do { finish(try await api.unlock(item.sha256, password: password, remember: remember)) } catch { self.error = error.localizedDescription }
                    }
                }
                .buttonStyle(.keelPrimary)
                .disabled(working || password.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            Toggle(L("记住此密码，后续账单自动尝试"), isOn: $remember).toggleStyle(.checkbox).font(.ui(12))
            Text(where_ + " " + L("勾选后保存在本机钥匙串；新导出若换了密码，仍需补填。")).font(.ui(11.5)).foregroundStyle(Theme.muted)
            if let error { ErrorLine(message: error) }
        }
    }

    private var where_: String {
        switch item.institution {
        case "Alipay": return L("密码在支付宝 App 的消息里（申请流水证明后发送）。")
        case "WeChat": return L("密码在微信「微信支付」的消息里，用最新收到的那一条。")
        default: return L("密码在发送账单的 App 里。")
        }
    }
}

/// An unknown CSV / Excel layout: name the columns once, Keel remembers the layout.
private struct TeachForm: View {
    @Environment(AppModel.self) private var model
    let item: PendingImport
    let finish: (ImportResult) -> Void
    @State private var date = ""
    @State private var description = ""
    @State private var amount = ""
    @State private var debit = ""
    @State private var credit = ""
    @State private var balance = ""
    @State private var split = false
    @State private var institution = ""
    @State private var accountType = "Assets"
    @State private var currency = "USD"
    @State private var dateFormat = ""
    @State private var sign = "as_is"
    @State private var working = false
    @State private var error: String?

    private var header: [String] { item.preview?.header ?? [] }

    private var formats: [String] {
        var seen = Set<String>()
        let all = (item.preview?.guesses?.dateFormats ?? []) + [
            "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%m/%d/%y", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%d %H:%M", "%Y年%m月%d日", "%Y%m%d",  // l10n-allow: date formats
        ]
        return all.filter { seen.insert($0).inserted }
    }

    var body: some View {
        if header.isEmpty {
            Text(L("没有可映射的表头。请检查文件是否完整，并导出 CSV 或 Excel 后重新上传。")).font(.ui(13)).foregroundStyle(Theme.muted)
        } else {
            VStack(alignment: .leading, spacing: 16) {
                preview
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 14, alignment: .leading), count: 3), alignment: .leading, spacing: 14) {
                    column(L("日期列"), $date)
                    column(L("描述列"), $description)
                    choice(L("金额方式"), Binding(get: { split ? "split" : "amount" }, set: { split = $0 == "split" }), [("amount", L("单列金额")), ("split", L("支出 + 收入"))])
                    if split {
                        column(L("支出列"), $debit)
                        column(L("收入列"), $credit)
                    } else {
                        column(L("金额列"), $amount)
                    }
                    column(L("余额列（可选）"), $balance, optional: true)
                    choice(L("日期格式"), $dateFormat, formats.map { ($0, $0) })
                    choice(L("金额方向"), $sign, [("as_is", L("支出是负数")), ("negate", L("支出是正数"))])
                    Field(label: L("机构名称")) { KeelField(placeholder: "", text: $institution, width: nil) }
                    choice(L("账户类型"), $accountType, [("Assets", L("资产")), ("Liabilities", L("负债"))])
                    choice(L("币种"), $currency, [("USD", "USD"), ("CNY", "CNY")])
                }
                if let error { ErrorLine(message: error) }
                Button(working ? L("正在学习…") : L("教会它")) { Task { await teach() } }
                    .buttonStyle(.keelPrimaryCompact)
                    .disabled(working || date.isEmpty || description.isEmpty || institution.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            .onAppear(perform: guess)
        }
    }

    private var preview: some View {
        ScrollView(.horizontal) {
            Grid(alignment: .leading, horizontalSpacing: 0, verticalSpacing: 0) {
                GridRow {
                    ForEach(Array(header.enumerated()), id: \.offset) { _, h in
                        Text(h).font(.ui(11.5, .medium)).foregroundStyle(Theme.ink).padding(.horizontal, 10).padding(.vertical, 7)
                    }
                }
                .background(Theme.fill.opacity(0.6))
                ForEach(Array((item.preview?.rows ?? []).prefix(5).enumerated()), id: \.offset) { _, row in
                    GridRow {
                        ForEach(0..<header.count, id: \.self) { j in
                            Text(j < row.count ? row[j] : "").font(.mono(11)).foregroundStyle(Theme.ink).lineLimit(1)
                                .padding(.horizontal, 10).padding(.vertical, 6)
                        }
                    }
                }
            }
        }
        .background(Theme.card)
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Theme.border))
    }

    private func column(_ label: String, _ value: Binding<String>, optional: Bool = false) -> some View {
        choice(label, value, [("", optional ? L("不使用") : L("选择列"))] + header.filter { !$0.isEmpty }.map { ($0, $0) })
    }

    private func choice(_ label: String, _ value: Binding<String>, _ options: [(String, String)]) -> some View {
        Field(label: label) {
            Picker(label, selection: value) {
                ForEach(options, id: \.0) { Text($0.1).tag($0.0) }
            }
            .labelsHidden()
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func guess() {
        let g = item.preview?.guesses
        date = g?.date ?? ""
        description = g?.description ?? ""
        amount = g?.amount ?? ""
        debit = g?.debit ?? ""
        credit = g?.credit ?? ""
        balance = g?.balance ?? ""
        split = g?.amount == nil && g?.debit != nil && g?.credit != nil
        institution = item.institution ?? ""
        dateFormat = formats.first ?? "%Y-%m-%d"
    }

    private func teach() async {
        guard let api = model.api else { return }
        working = true
        defer { working = false }
        let spec = ProfileSpec(
            institution: institution.trimmingCharacters(in: .whitespaces), accountType: accountType, currency: currency,
            header: header, dateFormat: dateFormat, sign: sign,
            columns: .init(
                date: date, description: description, amount: split ? nil : amount, debit: split ? debit : nil,
                credit: split ? credit : nil, balance: balance.isEmpty ? nil : balance
            )
        )
        do {
            finish(try await api.teach(item.sha256, spec: spec))
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}
