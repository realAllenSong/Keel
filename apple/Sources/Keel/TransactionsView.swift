import KeelCore
import SwiftUI

/// 交易: every transaction, pending first. Changing a category also teaches a rule for that
/// merchant (as on the web), so next month it is automatic.
struct TransactionsView: View {
    @Environment(AppModel.self) private var model
    @State private var page = Loader<TxnPage>("transactions.page")
    @State private var categories: [String] = []
    @State private var query = ""
    @State private var working = false
    @State private var adding = false
    @State private var editing: Txn?
    @State private var splitting: Txn?
    @State private var deleting: Txn?
    @State private var tagTotals: [TagTotal] = []
    @State private var tag: String?
    @State private var inFlight: InFlight?
    @AppStorage("keel.netWorthCurrency") private var currency = "USD"

    var body: some View {
        @Bindable var model = model
        let items = page.value?.items ?? []
        let pending = items.filter(\.isPending)
        VStack(alignment: .leading, spacing: 24) {
            VStack(alignment: .leading, spacing: 8) {
                Text(L("每一笔，都有来处")).font(.display(32)).foregroundStyle(Theme.ink)
                Text(L("待确认的先看。改一次类别，以后同一商户就会自动归类。")).font(.ui(13)).foregroundStyle(Theme.muted)
            }
            HStack(spacing: 12) {
                KeelField(placeholder: L("搜索描述 / 商户"), text: $query, width: 256, icon: "magnifyingglass")
                Segmented(
                    items: [("", L("全部")), ("pending", L("待确认")), ("confirmed", L("已确认"))],
                    selection: $model.txnStatus, fill: true
                )
                Spacer()
                Button(L("记一笔")) { adding = true }.buttonStyle(.keelPrimary)
                    .help(L("没有账单的钱：现金、朋友还你的钱"))
            }
            if !tagTotals.isEmpty {
                // Tags cut across categories: what a trip or a move cost in all.
                HStack(spacing: 14) {
                    Text(L("标签")).font(.ui(12)).foregroundStyle(Theme.muted)
                    ForEach(tagTotals.prefix(8)) { t in
                        Button { tag = tag == t.tag ? nil : t.tag } label: {
                            HStack(spacing: 6) {
                                Text(t.tag).font(.ui(12.5, tag == t.tag ? .semibold : .regular)).foregroundStyle(Theme.ink)
                                Text(Format.money(t.spent, currency: currency)).font(.mono(11.5)).foregroundStyle(Theme.muted)
                            }
                            .padding(.bottom, 3)
                            .overlay(alignment: .bottom) {
                                Rectangle().fill(tag == t.tag ? Theme.ink : .clear).frame(height: 1)
                            }
                        }
                        .buttonStyle(.plain).pointingHandCursor()
                        .help(L("{n} 笔，{a} 到 {b}", ["n": t.count, "a": Format.day(t.first), "b": Format.day(t.last)]))
                    }
                }
            }
            if model.txnMonth != nil || model.txnCategory != nil || model.txnAccount != nil {
                HStack(spacing: 8) {
                    if let m = model.txnMonth { FilterChip(text: m.count == 10 ? Format.day(m) : Format.month(m)) { model.txnMonth = nil } }
                    if let c = model.txnCategory { FilterChip(text: L("类别") + L("：") + Format.account(c)) { model.txnCategory = nil } }
                    if let a = model.txnAccount { FilterChip(text: L("账户") + L("：") + Format.account(a)) { model.txnAccount = nil } }
                }
            }
            if let f = inFlight, !f.lines.isEmpty, model.txnMonth == nil, model.txnCategory == nil, tag == nil {
                InFlightRow(
                    title: L("银行还有 {n} 笔没入账，入账后会自动出现在下面，不计入任何合计", ["n": f.lines.count]),
                    lines: model.txnAccount.map { a in f.lines.filter { $0.account == a } } ?? f.lines
                )
            }
            if model.txnStatus != "confirmed" {
                ReviewGroups(categories: categories)
            }
            if model.txnStatus != "confirmed", !pending.isEmpty {
                ReviewBar(count: pending.count, working: working, canAccept: pending.contains(where: \.hasSuggestion)) {
                    Task { await acceptAll() }
                }
            }
            if let error = page.error {
                ErrorLine(message: L("交易暂时读不到：{e}", ["e": error]))
            } else if page.value == nil {
                VStack(spacing: 10) {
                    ForEach(0..<6, id: \.self) { _ in RoundedRectangle(cornerRadius: 6).fill(Theme.fill.opacity(0.6)).frame(height: 36) }
                }
            } else if items.isEmpty {
                Text(model.txnStatus == "pending" ? L("全部确认完毕。") : L("没有符合条件的交易。试试调整筛选，或把账单拖进来。"))
                    .font(.ui(14)).foregroundStyle(Theme.muted).padding(.vertical, 24)
            } else {
                TxnTable(
                    items: items, categories: categories, setCategory: setCategory,
                    accept: { id in Task { await accept([id]) } },
                    act: { action, t in
                        switch action {
                        case .edit: editing = t
                        case .split: splitting = t
                        case .delete: deleting = t
                        }
                    }
                )
                if let p = page.value {
                    Text(L("共 {n} 笔", ["n": p.total])).font(.mono(11.5)).foregroundStyle(Theme.muted)
                }
            }
        }
        .task(id: "\(model.revision)|\(model.txnStatus)|\(query)|\(model.txnMonth ?? "")|\(model.txnCategory ?? "")|\(model.txnAccount ?? "")|\(tag ?? "")") {
            try? await Task.sleep(for: .milliseconds(query.isEmpty ? 0 : 250))  // debounce typing
            await page.load { [status = model.txnStatus, query, month = model.txnMonth, category = model.txnCategory, account = model.txnAccount, tag] in
                try await $0.transactions(
                    q: query.isEmpty ? nil : query, status: status.isEmpty ? nil : status,
                    month: month, category: category, account: account, tag: tag
                )
            }
        }
        .task(id: "\(model.revision)|\(currency)") {
            guard let api = model.api else { return }
            inFlight = try? await api.inFlight(currency: currency)
            if let list = try? await api.categories() { categories = list }
            if let totals = try? await api.tagTotals(currency: currency) {
                tagTotals = totals
                if let tag, !totals.contains(where: { $0.tag == tag }) { self.tag = nil }
            }
        }
        .sheet(isPresented: $adding) { ManualEntrySheet(categories: categories).environment(model) }
        .sheet(item: $editing) { EditTxnSheet(txn: $0).environment(model) }
        .sheet(item: $splitting) { SplitSheet(txn: $0, categories: categories).environment(model) }
        .confirmationDialog(
            L("删除这一笔？"),
            isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
            presenting: deleting
        ) { t in
            Button(L("删除"), role: .destructive) { Task { await delete(t) } }
        } message: { t in
            Text(t.isManual ? L("这是你手动记的，删除后就没有了。") : L("以后重新导入同一份账单，这一笔也不会再出现。"))
        }
    }

    private func delete(_ t: Txn) async {
        guard let api = model.api else { return }
        do {
            try await api.deleteTransaction(t.id)
            model.banner = L("已删除")
            model.revision += 1
            await model.refreshBadge()
        } catch {
            model.banner = L("删除失败") + L("：") + error.localizedDescription
        }
    }

    private func setCategory(_ t: Txn, _ category: String) {
        guard let api = model.api else { return }
        Task {
            do {
                let r = try await api.setCategory(t.id, category)
                model.banner = L("已归类为 {c}", ["c": Format.account(category)])
                    + (r.alsoUpdated > 0 ? " · " + L("并按新规则自动归类了另外 {n} 笔", ["n": r.alsoUpdated]) : "")
                model.revision += 1
                await model.refreshBadge()
            } catch {
                model.banner = L("归类失败") + L("：") + error.localizedDescription
            }
        }
    }

    private func accept(_ ids: [Int]) async {
        guard let api = model.api, !ids.isEmpty else { return }
        working = true
        defer { working = false }
        do {
            try await api.confirm(ids)
            model.revision += 1
            await model.refreshBadge()
        } catch {
            model.banner = L("确认失败") + L("：") + error.localizedDescription
        }
    }

    private func acceptAll() async {
        let ids = (page.value?.items ?? []).filter { $0.isPending && $0.hasSuggestion }.map(\.id)
        await accept(ids)
        model.banner = L("已接受全部建议")
    }
}

/// "这里有 56 笔等你确认" with an amber rule on the left, and 全部接受建议.
private struct ReviewBar: View {
    let count: Int
    let working: Bool
    let canAccept: Bool
    let acceptAll: () -> Void

    var body: some View {
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text(L("这里有 {n} 笔等你确认", ["n": count])).font(.ui(13)).foregroundStyle(Theme.ink)
                Text(L("类别有建议的点「确认」；不对就点类别改掉。")).font(.ui(11.5)).foregroundStyle(Theme.muted)
            }
            Spacer()
            if canAccept {
                Button(L("全部接受建议"), action: acceptAll).buttonStyle(.keelPrimaryCompact).disabled(working)
            }
        }
        .padding(.leading, 16)
        .padding(.vertical, 8)
        .overlay(alignment: .leading) { Rectangle().fill(Theme.signal).frame(width: 2) }
    }
}

enum TxnAction { case edit, split, delete }

private struct TxnTable: View {
    let items: [Txn]
    let categories: [String]
    let setCategory: (Txn, String) -> Void
    let accept: (Int) -> Void
    let act: (TxnAction, Txn) -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                header(L("日期")).frame(width: 96, alignment: .leading)
                header(L("描述")).frame(maxWidth: .infinity, alignment: .leading)
                header(L("账户")).frame(width: 170, alignment: .leading)
                header(L("类别")).frame(width: 320, alignment: .leading)
                header(L("金额")).frame(width: 120, alignment: .trailing)
            }
            .padding(.vertical, 8)
            Hairline(color: Theme.rule)
            LazyVStack(spacing: 0) {
                ForEach(items) { t in
                    TxnRow(txn: t, categories: categories, setCategory: setCategory, accept: accept)
                        .contextMenu {
                            Button(L("编辑…")) { act(.edit, t) }
                            if !t.isTransfer && !t.isManual { Button(L("拆成几类…")) { act(.split, t) } }
                            Divider()
                            Button(L("删除…"), role: .destructive) { act(.delete, t) }
                        }
                    Hairline(color: Theme.border.opacity(0.6))
                }
            }
        }
    }

    private func header(_ s: String) -> some View {
        Text(s).font(.ui(11)).foregroundStyle(Theme.muted)
    }
}

private struct TxnRow: View {
    let txn: Txn
    let categories: [String]
    let setCategory: (Txn, String) -> Void
    let accept: (Int) -> Void
    @State private var hover = false

    var body: some View {
        HStack(spacing: 12) {
            Text(txn.date).font(.mono(12)).foregroundStyle(Theme.muted).frame(width: 96, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                Text(txn.payee.flatMap { txn.isTransfer ? nil : $0 } ?? Format.tidy(txn.description))
                    .help(txn.description)
                    .font(.ui(13)).foregroundStyle(Theme.ink).lineLimit(1)
                if let sub = txn.isTransfer ? txn.note : (txn.payee == nil ? nil : txn.description) {
                    Text(sub).font(.ui(11)).foregroundStyle(Theme.muted).lineLimit(1)
                }
                if let tags = txn.tags, !tags.isEmpty {
                    Text(tags.map { "#" + $0 }.joined(separator: "  ")).font(.ui(11)).foregroundStyle(Theme.ink2).lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(Format.account(txn.account)).font(.ui(13)).foregroundStyle(Theme.muted).lineLimit(1)
                .frame(width: 170, alignment: .leading)
            category.frame(width: 320, alignment: .leading)
            Text(Format.money(txn.amount, currency: txn.commodity, digits: 2, sign: !txn.amount.isNegative))
                .font(.mono(13))
                .foregroundStyle(txn.isTransfer ? Theme.ink : txn.amount.isNegative ? Theme.outflow : Theme.inflow)
                .frame(width: 120, alignment: .trailing)
        }
        .padding(.vertical, txn.payee == nil ? 11 : 7)
        .padding(.horizontal, 4)
        .background(rowBackground)
        .onHover { hover = $0 }
    }

    private var rowBackground: Color {
        if txn.isPending { return Theme.signal.opacity(hover ? 0.15 : 0.1) }
        return hover ? Theme.fill.opacity(0.5) : .clear
    }

    private var category: some View {
        HStack(spacing: 6) {
            if txn.isTransfer {
                Text("→ " + Format.account(txn.counterAccount)).font(.ui(13)).foregroundStyle(Theme.muted).lineLimit(1)
            } else if txn.isSplit {
                Text(L("拆成 {n} 类", ["n": txn.parts.count])).font(.ui(13)).foregroundStyle(Theme.ink)
                    .help(txn.parts.map { Format.account($0.account) + " " + Format.money(abs(Decimal(string: $0.amount) ?? 0), digits: 2) }.joined(separator: "\n"))
            } else {
                Menu {
                    ForEach(["Expenses", "Income", "Assets", "Liabilities"], id: \.self) { root in
                        let options = categories.filter { $0.hasPrefix(root + ":") }
                        if !options.isEmpty {
                            Section(groupName(root)) {
                                ForEach(options, id: \.self) { c in
                                    Button(Format.account(c)) { setCategory(txn, c) }
                                }
                            }
                        }
                    }
                } label: {
                    Text(Format.account(txn.counterAccount))
                        .font(.ui(13))
                        .foregroundStyle(uncategorized ? Theme.muted : Theme.ink)
                        .italic(uncategorized)
                        .lineLimit(1)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(hover ? Theme.card : .clear, in: RoundedRectangle(cornerRadius: 5))
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
            }
            if txn.isPending && txn.hasSuggestion {
                Button(L("确认")) { accept(txn.id) }
                    .buttonStyle(.plain)
                    .font(.ui(12))
                    .foregroundStyle(Theme.ink)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(hover ? Theme.card : .clear, in: RoundedRectangle(cornerRadius: 5))
            }
            if let tag = tagText {
                Text(tag)
                    .font(.ui(10))
                    .foregroundStyle(Theme.muted)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .overlay(
                        RoundedRectangle(cornerRadius: 4)
                            .strokeBorder(Theme.rule, style: StrokeStyle(lineWidth: 1, dash: txn.isPending ? [2, 2] : []))
                    )
            }
        }
    }

    private var uncategorized: Bool { (txn.counterAccount ?? "").hasSuffix(":Uncategorized") }

    private var tagText: String? {
        if txn.isTransfer { return txn.isPending ? L("疑似转账") : L("转账") }
        if txn.isManual { return L("手记") }
        if txn.edited { return L("改过") }
        switch txn.categorySource {
        case "user": return L("手动")
        case "override": return L("已确认")
        case "rule": return L("规则")
        case "seed": return L("内置")
        case "llm": return L("AI 建议")
        case "learned": return L("按你的习惯")
        case "hint": return L("银行建议")
        default: return nil
        }
    }

    private func groupName(_ root: String) -> String {
        switch root {
        case "Expenses": return L("支出")
        case "Income": return L("收入")
        case "Assets": return L("转入自己的资产账户（非支出）")
        default: return L("负债")
        }
    }
}

/// An active filter, removable with ×.
private struct FilterChip: View {
    let text: String
    let clear: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            Text(text).font(.ui(12)).foregroundStyle(Theme.ink)
            Button(action: clear) {
                Image(systemName: "xmark").font(.ui(9, .semibold)).foregroundStyle(Theme.muted)
            }
            .buttonStyle(.plain)
            .help(L("清除"))
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(Theme.card, in: Capsule())
        .overlay(Capsule().strokeBorder(Theme.border))
    }
}

/// What nobody could categorise, one row per merchant or person: one choice files every line of
/// the group and is remembered for the next ones (engine: pf/ledger/groups.py).
private struct ReviewGroups: View {
    @Environment(AppModel.self) private var model
    let categories: [String]
    @State private var groups: [ReviewGroup] = []
    @State private var showAll = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) { content }.task(id: model.revision) {
            if let api = model.api, let list = try? await api.reviewGroups() { groups = list }
        }
    }

    @ViewBuilder private var content: some View {
        if !groups.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(L("按商户或对方一次处理")).font(.display(20)).foregroundStyle(Theme.ink)
                    Text(L("银行给了类别、或和你以前确认过的很像的，已经自动归好类。剩下的按商户或人分组：选一次类别，这一组和以后同样的都会归过去。"))
                        .font(.ui(12.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                }
                VStack(spacing: 0) {
                    ForEach(showAll ? groups : Array(groups.prefix(12))) { g in
                        row(g)
                        Hairline()
                    }
                }
                if groups.count > 12 {
                    LinkButton(title: showAll ? L("收起") : L("再看 {n} 组", ["n": groups.count - 12]), arrow: false) { showAll.toggle() }
                }
            }
        }
    }

    private func row(_ g: ReviewGroup) -> some View {
        let incoming = g.direction == "in"
        let options = categories.filter { $0.hasPrefix(incoming ? "Income:" : "Expenses:") || $0.hasPrefix("Assets:") || $0.hasPrefix("Liabilities:") }
        return HStack(alignment: .center, spacing: 16) {
            VStack(alignment: .leading, spacing: 3) {
                Text(Format.tidy(g.key.uppercased())).font(.ui(13.5, .medium)).foregroundStyle(Theme.ink).lineLimit(1)
                Text(L("{n} 笔 · {when} · {accounts}", [
                    "n": g.count,
                    "when": g.first == g.last ? Format.day(g.first) : Format.day(g.first) + " – " + Format.day(g.last),
                    "accounts": g.accounts.map { Format.account($0) }.joined(separator: "、"),
                ]))
                .font(.ui(11.5)).foregroundStyle(Theme.muted).lineLimit(1)
                if let hint = g.hint {
                    Text(hint).font(.ui(11.5)).foregroundStyle(Theme.signalInk)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 12)
            Text((incoming ? L("收") : L("付")) + " " + Format.money(abs(g.total.value), digits: 0))
                .font(.mono(12.5)).foregroundStyle(incoming ? Theme.inflow : Theme.ink)
            CategoryMenu(
                options: options,
                selection: Binding(get: { "" }, set: { c in Task { await apply(g, c) } }),
                placeholder: L("选类别")
            )
            .frame(minWidth: 120, alignment: .trailing)
        }
        .padding(.vertical, 9)
    }

    private func apply(_ g: ReviewGroup, _ category: String) async {
        guard let api = model.api else { return }
        do {
            let r = try await api.applyGroup(g, category: category)
            model.banner = L("{n} 笔已归为 {c}，以后同样的也会自动归类", ["n": r.count, "c": Format.account(category)])
            groups.removeAll { $0.id == g.id }
            model.revision += 1
            await model.refreshBadge()
        } catch {
            model.banner = error.localizedDescription
        }
    }
}
