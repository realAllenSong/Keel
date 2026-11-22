import KeelCore
import SwiftUI

/// "1,234.5" -> 1234.5 (nil when it isn't a number).
func parseAmount(_ s: String) -> Decimal? {
    let clean = s.replacingOccurrences(of: ",", with: "").replacingOccurrences(of: "$", with: "")
        .trimmingCharacters(in: .whitespaces)
    guard !clean.isEmpty else { return nil }
    return Decimal(string: clean, locale: Locale(identifier: "en_US_POSIX"))
}

func amountString(_ d: Decimal) -> String { NSDecimalNumber(decimal: d).stringValue }

private func isoDay(_ d: Date) -> String {
    var c = Calendar(identifier: .gregorian)
    c.timeZone = .current
    let p = c.dateComponents([.year, .month, .day], from: d)
    return String(format: "%04d-%02d-%02d", p.year ?? 0, p.month ?? 0, p.day ?? 0)
}

private func dateFrom(_ iso: String) -> Date {
    let p = iso.split(separator: "-").compactMap { Int($0) }
    guard p.count == 3 else { return Date() }
    return Calendar(identifier: .gregorian).date(from: DateComponents(year: p[0], month: p[1], day: p[2])) ?? Date()
}

/// The shell every editor shares: a title, the form, an error line, 取消 and the main action.
private struct EditorSheet<Form: View>: View {
    let title: String
    let action: String
    let canSave: Bool
    var error: String?
    let save: () -> Void
    @ViewBuilder var form: Form
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(title).font(.display(20)).foregroundStyle(Theme.ink)
            form
            if let error { ErrorLine(message: error) }
            HStack {
                Spacer()
                Button(L("取消")) { dismiss() }.buttonStyle(.keelOutline).keyboardShortcut(.cancelAction)
                Button(action, action: save).buttonStyle(.keelPrimary).disabled(!canSave).keyboardShortcut(.defaultAction)
            }
        }
        .padding(28)
        .frame(width: 480)
        .background(Theme.card)
    }
}

private struct EditorRow<Content: View>: View {
    let label: String
    @ViewBuilder var content: Content
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(label).font(.ui(12.5)).foregroundStyle(Theme.muted).frame(width: 72, alignment: .leading)
            content
            Spacer(minLength: 0)
        }
    }
}

/// A menu of categories (or accounts), grouped by kind, showing the chosen one.
struct CategoryMenu: View {
    let options: [String]
    @Binding var selection: String
    var placeholder: String = L("选择类别")

    var body: some View {
        Menu {
            ForEach(["Expenses", "Income", "Assets", "Liabilities"], id: \.self) { root in
                let group = options.filter { $0.hasPrefix(root + ":") }
                if !group.isEmpty {
                    Section(Self.groupName(root)) {
                        ForEach(group, id: \.self) { c in Button(Format.account(c)) { selection = c } }
                    }
                }
            }
        } label: {
            Text(selection.isEmpty ? placeholder : Format.account(selection))
                .font(.ui(13)).foregroundStyle(selection.isEmpty ? Theme.muted : Theme.ink).lineLimit(1)
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
    }

    static func groupName(_ root: String) -> String {
        switch root {
        case "Expenses": return L("支出")
        case "Income": return L("收入")
        case "Assets": return L("转入自己的资产账户（非支出）")
        default: return L("负债")
        }
    }
}

// MARK: - 记一笔

/// Money that moved without a statement: cash, a friend paying you back.
struct ManualEntrySheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let categories: [String]
    @State private var outflow = true
    @State private var date = Date()
    @State private var amount = ""
    @State private var account = "Assets:Cash:Wallet"
    @State private var category = ""
    @State private var text = ""
    @State private var accounts: [String] = []
    @State private var error: String?

    var body: some View {
        EditorSheet(title: L("记一笔"), action: L("保存"), canSave: canSave, error: error, save: save) {
            VStack(alignment: .leading, spacing: 14) {
                Segmented(items: [(true, L("支出")), (false, L("收入"))], selection: $outflow)
                EditorRow(label: L("日期")) { DatePicker("", selection: $date, displayedComponents: .date).labelsHidden().fixedSize() }
                EditorRow(label: L("金额")) { KeelField(placeholder: "0.00", text: $amount, width: 160, mono: true) }
                EditorRow(label: outflow ? L("从哪付") : L("进了哪")) {
                    CategoryMenu(options: accounts, selection: $account, placeholder: L("选择账户"))
                }
                EditorRow(label: L("类别")) {
                    CategoryMenu(options: categories.filter { $0.hasPrefix(outflow ? "Expenses:" : "Income:") }, selection: $category)
                }
                EditorRow(label: L("说明")) { KeelField(placeholder: L("比如：路边摊午饭"), text: $text, width: 300) }
            }
        }
        .task {
            guard let api = model.api, let list = try? await api.accounts() else { return }
            var names = list.filter { ($0.type == "Assets" || $0.type == "Liabilities") && $0.flowOnly != true }.map(\.name)
            if !names.contains("Assets:Cash:Wallet") { names.insert("Assets:Cash:Wallet", at: 0) }
            accounts = names
        }
    }

    private var canSave: Bool { (parseAmount(amount) ?? 0) > 0 && !category.isEmpty && !account.isEmpty }

    private func save() {
        guard let api = model.api, let a = parseAmount(amount) else { return }
        let body = ManualIn(
            date: isoDay(date), account: account, amount: amountString(outflow ? -a : a),
            category: category, description: text.trimmingCharacters(in: .whitespaces)
        )
        Task {
            do {
                _ = try await api.addTransaction(body)
                model.revision += 1
                dismiss()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

// MARK: - 编辑

/// Change what a line says: date, amount, description, payee, note. Fixes to imported lines
/// are remembered, so re-importing the statement keeps them.
struct EditTxnSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let txn: Txn
    @State private var date = Date()
    @State private var amount = ""
    @State private var text = ""
    @State private var payee = ""
    @State private var note = ""
    @State private var tags = ""
    @State private var tagMerchant = false
    @State private var error: String?

    private var tagList: [String] {
        tags.split(whereSeparator: { ",，".contains($0) }).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }

    var body: some View {
        EditorSheet(title: L("编辑这一笔"), action: L("保存"), canSave: parseAmount(amount) != nil, error: error, save: save) {
            VStack(alignment: .leading, spacing: 14) {
                EditorRow(label: L("日期")) { DatePicker("", selection: $date, displayedComponents: .date).labelsHidden().fixedSize() }
                EditorRow(label: L("金额")) {
                    KeelField(placeholder: "0.00", text: $amount, width: 160, mono: true)
                    Text(L("负数 = 钱出去")).font(.ui(11)).foregroundStyle(Theme.muted)
                }
                EditorRow(label: L("说明")) { KeelField(placeholder: "", text: $text, width: 300) }
                EditorRow(label: L("商户")) { KeelField(placeholder: L("可留空"), text: $payee, width: 300) }
                EditorRow(label: L("备注")) { KeelField(placeholder: L("可留空"), text: $note, width: 300) }
                EditorRow(label: L("标签")) {
                    KeelField(placeholder: L("比如 日本旅行；多个用逗号隔开"), text: $tags, width: 300)
                }
                if !tagList.isEmpty {
                    EditorRow(label: "") {
                        Toggle(isOn: $tagMerchant) {
                            Text(L("这个商户以后都自动打上这些标签")).font(.ui(12.5)).foregroundStyle(Theme.ink)
                        }
                        .toggleStyle(.checkbox)
                    }
                }
                if !txn.isManual {
                    Text(L("账单里的原始记录不会被改动；重新导入时也会沿用你的修改。"))
                        .font(.ui(11.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .onAppear {
            date = dateFrom(txn.date)
            amount = txn.amount.raw
            text = txn.description
            payee = txn.payee ?? ""
            note = txn.note ?? ""
            tags = (txn.tags ?? []).joined(separator: L("，"))
        }
    }

    private func save() {
        guard let api = model.api else { return }
        var body = EditIn()
        if isoDay(date) != txn.date { body.date = isoDay(date) }
        if let a = parseAmount(amount), a != txn.amount.value { body.amount = amountString(a) }
        if text != txn.description { body.description = text }
        if payee != (txn.payee ?? "") { body.payee = payee }
        if note != (txn.note ?? "") { body.note = note }
        Task {
            do {
                // An edit can re-post the line under a new id: tag the line as it is afterwards.
                let saved = try await api.editTransaction(txn.id, body)
                if Set(tagList) != Set(txn.tags ?? []) { _ = try await api.setTags(saved.id, tagList) }
                if tagMerchant { for tag in tagList { try await api.addTagRule(likeTxn: saved.id, tag: tag) } }
                model.revision += 1
                dismiss()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

// MARK: - 拆分

/// Spread one line over several categories (a Costco run: groceries and household).
struct SplitSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let txn: Txn
    let categories: [String]
    @State private var rows: [SplitRow] = []

    struct SplitRow: Identifiable {
        let id = UUID()
        var category: String
        var amount: String
    }
    @State private var error: String?

    private var total: Decimal { abs(txn.amount.value) }
    private var assigned: Decimal { rows.compactMap { parseAmount($0.amount) }.reduce(0, +) }
    private var left: Decimal { total - assigned }

    var body: some View {
        EditorSheet(title: L("拆成几类"), action: L("保存"), canSave: canSave, error: error, save: save) {
            VStack(alignment: .leading, spacing: 12) {
                Text(L("这一笔 {x}，分到几个类别，加起来要等于总数。", ["x": Format.money(txn.amount.value, digits: 2)]))
                    .font(.ui(12.5)).foregroundStyle(Theme.muted)
                ForEach($rows) { $row in
                    HStack(spacing: 10) {
                        CategoryMenu(
                            options: categories.filter { $0.hasPrefix(txn.amount.isNegative ? "Expenses:" : "Income:") },
                            selection: $row.category
                        )
                        .frame(width: 220, alignment: .leading)
                        KeelField(placeholder: "0.00", text: $row.amount, width: 110, mono: true)
                        if rows.count > 2 {
                            Button { rows.removeAll { $0.id == row.id } } label: { Image(systemName: "minus.circle") }
                                .buttonStyle(.plain).foregroundStyle(Theme.muted)
                        }
                    }
                }
                HStack {
                    Button(L("再加一类")) { rows.append(SplitRow(category: "", amount: left > 0 ? amountString(left) : "")) }
                        .buttonStyle(.keelOutlineCompact)
                    Spacer()
                    Text(left == 0 ? L("刚好分完") : L("还差 {x}", ["x": Format.money(left, digits: 2)]))
                        .font(.mono(12)).foregroundStyle(left == 0 ? Theme.muted : Theme.signal)
                }
                if txn.isSplit {
                    Button(L("取消拆分，回到一个类别")) { undo() }.buttonStyle(.keelOutlineCompact)
                }
            }
        }
        .onAppear {
            if txn.isSplit {
                rows = txn.parts.map { SplitRow(category: $0.account, amount: amountString(abs(Decimal(string: $0.amount) ?? 0))) }
            } else {
                let current = (txn.counterAccount ?? "").hasSuffix(":Uncategorized") ? "" : (txn.counterAccount ?? "")
                rows = [SplitRow(category: current, amount: amountString(total)), SplitRow(category: "", amount: "")]
            }
        }
    }

    private var canSave: Bool {
        rows.count >= 2 && left == 0 && rows.allSatisfy { !$0.category.isEmpty && (parseAmount($0.amount) ?? 0) > 0 }
    }

    private func save() {
        let parts = rows.map { TxnPart(account: $0.category, amount: amountString(parseAmount($0.amount) ?? 0)) }
        run(parts)
    }

    private func undo() { run([]) }

    private func run(_ parts: [TxnPart]) {
        guard let api = model.api else { return }
        Task {
            do {
                _ = try await api.splitTransaction(txn.id, parts)
                model.revision += 1
                dismiss()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}
