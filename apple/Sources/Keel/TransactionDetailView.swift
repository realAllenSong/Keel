import KeelCore
import SwiftUI

struct TransactionDetail: Identifiable {
    let month: String
    var category: String?
    var account: String?
    var status: String?
    var cashflowOnly = false
    var through: String?
    var currency = "USD"
    var id: String { "\(month)|\(category ?? "")|\(account ?? "")|\(status ?? "")|\(cashflowOnly)|\(through ?? "")|\(currency)" }
}

/// Inspect the lines behind a report without losing the selected month or category.
struct TransactionDetailView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let detail: TransactionDetail
    @State private var items: [Txn] = []
    @State private var summary: TxnPage?
    @State private var showingOriginal = false
    @State private var loadID: UUID?
    @State private var loading = true
    @State private var error: String?
    @State private var categories: [String] = []
    @State private var working = false
    @State private var editing: Txn?
    @State private var splitting: Txn?
    @State private var adding = false
    @State private var sortOrder: TransactionSort = .amountDescending

    private var sortedItems: [Txn] { TransactionOrdering.sorted(items, by: sortOrder, category: detail.category, cashflowOnly: detail.cashflowOnly, currency: detail.currency) }
    private var periodLabel: String {
        if detail.month.isEmpty { return L("所有月份") }
        if detail.month.count == 4 { return L("{y} 年", ["y": detail.month]) }
        return detail.month.count == 10 ? Format.day(detail.month) : Format.month(detail.month)
    }

    private func amount(_ t: Txn) -> Decimal {
        TransactionOrdering.amount(t, category: detail.category, cashflowOnly: detail.cashflowOnly)
    }

    private func converted(_ t: Txn) -> Decimal? {
        TransactionOrdering.convertedAmount(t, category: detail.category, cashflowOnly: detail.cashflowOnly, currency: detail.currency)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(detail.category.map { Format.account($0) } ?? L("交易明细"))
                        .font(.display(24)).foregroundStyle(Theme.ink)
                    Text(periodLabel + " · " + L("共 {n} 笔", ["n": items.count]))
                        .font(.ui(13)).foregroundStyle(Theme.muted)
                }
                Spacer()
                Button(L("记一笔")) { adding = true }.buttonStyle(.keelOutlineCompact).disabled(categories.isEmpty)
                Button(L("关闭")) { dismiss() }.buttonStyle(.keelOutlineCompact).keyboardShortcut(.cancelAction)
            }
            if loading {
                SkeletonRows()
            } else if let error {
                ErrorLine(message: error)
                Button(L("重试")) { Task { await load() } }.buttonStyle(.keelOutlineCompact)
            } else if items.isEmpty {
                Text(L("没有符合条件的交易。")).font(.ui(13)).foregroundStyle(Theme.muted)
            } else {
                HStack(spacing: 18) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(L("合计 · {c}", ["c": detail.currency])).font(.ui(12)).foregroundStyle(Theme.muted)
                        Text(summary?.displayTotal.map { Format.money($0, currency: detail.currency, digits: 2) } ?? "—")
                            .font(.mono(24)).foregroundStyle(Theme.ink)
                    }
                    Spacer()
                    Picker(L("排序"), selection: $sortOrder) {
                        Text(L("金额：从高到低")).tag(TransactionSort.amountDescending)
                        Text(L("金额：从低到高")).tag(TransactionSort.amountAscending)
                        Text(L("日期：从新到旧")).tag(TransactionSort.dateDescending)
                    }.pickerStyle(.menu).font(.ui(12)).frame(width: 190)
                }
                if summary?.displayTotal == nil, let missing = summary?.missingFx, !missing.isEmpty {
                    Text(L("{c} 缺少汇率，完整收支总额暂不可用；明细仍可查看原币。", ["c": missing.joined(separator: ", ")])).font(.ui(11.5)).foregroundStyle(Theme.signalInk)
                }
                if let original = summary?.originalTotals, original.keys.contains(where: { $0 != detail.currency }) {
                    DisclosureGroup(L("原币小计（分币种）"), isExpanded: $showingOriginal) {
                        VStack(alignment: .leading, spacing: 6) {
                            ForEach(original.keys.sorted(), id: \.self) { code in
                                if let value = original[code] {
                                    Text(code + "  " + Format.money(value, currency: code, digits: 2)).font(.mono(12))
                                }
                            }
                        }.padding(.top, 6)
                    }.font(.ui(12)).foregroundStyle(Theme.muted)
                    Text(L("按显示币种排序。历史月份用截至月末的汇率，本月用截至报表日期的汇率。缺汇率的交易排在最后。"))
                        .font(.ui(11.5)).foregroundStyle(Theme.muted)
                }
                Text(detail.category == nil
                     ? (detail.cashflowOnly ? L("收入减去支出；内部转账、估值变动和期初余额不计入。") : L("金额按账户流水显示，内部转账不视作消费。"))
                     : detail.category == "Income" || detail.category?.hasPrefix("Income:") == true
                         ? L("金额按收入来源计入；拆分交易只显示分到该来源的部分。")
                         : L("金额按所选类别计入；拆分交易只显示分到该类别的部分，退款会抵减花销。"))
                    .font(.ui(11.5)).foregroundStyle(Theme.muted)
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(sortedItems) { t in
                            HStack(alignment: .top, spacing: 16) {
                                Text(Format.day(t.date)).font(.mono(11.5)).foregroundStyle(Theme.muted).frame(width: 72, alignment: .leading)
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(Format.tidy(t.payee ?? t.description)).font(.ui(14, .medium)).foregroundStyle(Theme.ink)
                                    Text(Format.account(t.account)).font(.ui(12)).foregroundStyle(Theme.muted)
                                    if t.description != (t.payee ?? t.description) {
                                        Text(t.description).font(.ui(11.5)).foregroundStyle(Theme.muted)
                                            .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                                    }
                                    categoryControl(t)
                                    if let note = t.note, !note.isEmpty { Text(note).font(.ui(11.5)).foregroundStyle(Theme.muted) }
                                }
                                Spacer(minLength: 16)
                                VStack(alignment: .trailing, spacing: 8) {
                                    Text(converted(t).map { Format.money($0, currency: detail.currency, digits: 2) } ?? L("未换算"))
                                        .font(.mono(14)).foregroundStyle(Theme.ink)
                                    let original = t.reportOriginalAmounts?.mapValues(\.value) ?? [t.commodity: amount(t)]
                                    ForEach(original.keys.sorted().filter { $0 != detail.currency }, id: \.self) { code in
                                        if let value = original[code] {
                                            Text(L("原币 {a}", ["a": code + " " + Format.money(value, currency: code, digits: 2)]))
                                                .font(.mono(11)).foregroundStyle(Theme.muted)
                                        }
                                    }
                                    Menu(L("修改")) {
                                        Button(L("编辑")) { editing = t }
                                        if !t.isTransfer { Button(L("拆分")) { splitting = t } }
                                    }.menuStyle(.borderlessButton).fixedSize().font(.ui(11.5))
                                    if t.canConfirm {
                                        Button(L("确认")) { Task { await confirm(t) } }
                                            .buttonStyle(.keelOutlineCompact).disabled(working)
                                    }
                                }
                            }
                            .padding(.vertical, 14)
                            Hairline()
                        }
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(28).frame(width: 760, height: 600).background(Theme.paper)
        .task(id: "\(detail.id)|\(model.revision)") { await load() }
        .sheet(item: $editing) { EditTxnSheet(txn: $0).environment(model) }
        .sheet(item: $splitting) { SplitSheet(txn: $0, categories: categories).environment(model) }
        .sheet(isPresented: $adding) { ManualEntrySheet(categories: categories).environment(model) }
    }

    @ViewBuilder private func categoryControl(_ t: Txn) -> some View {
        if t.isTransfer || t.isSplit {
            Text(t.isSplit ? t.parts.map { Format.account($0.account) }.joined(separator: " · ") : Format.account(t.counterAccount))
                .font(.ui(11.5)).foregroundStyle(Theme.muted)
        } else {
            Menu {
                ForEach(["Expenses", "Income", "Assets", "Liabilities"], id: \.self) { root in
                    let options = categories.filter { $0.hasPrefix(root + ":") }
                    if !options.isEmpty {
                        Section(Format.segment(root)) {
                            ForEach(options, id: \.self) { category in
                                Button(Format.account(category)) { Task { await categorize(t, as: category) } }
                            }
                        }
                    }
                }
            } label: {
                Text(Format.account(t.counterAccount)).font(.ui(11.5)).foregroundStyle(Theme.ink)
            }
            .menuStyle(.borderlessButton).fixedSize().disabled(working || categories.isEmpty)
            .help(L("修改这一笔的类别"))
        }
    }

    private func categorize(_ txn: Txn, as category: String) async {
        guard let api = model.api else { return }
        working = true
        defer { working = false }
        do {
            // A contextual correction affects this transaction only; never rewrite other merchants
            // or months merely because a report row was changed.
            let _: CategorizeOut = try await api.post("api/transactions/\(txn.id)/category", CategorizeIn(category: category, remember: "override"))
            model.revision += 1
            await model.refreshBadge()
        } catch { self.error = error.localizedDescription }
    }

    private func confirm(_ txn: Txn) async {
        guard let api = model.api else { return }
        working = true
        defer { working = false }
        do {
            try await api.confirm([txn.id])
            model.revision += 1
            await model.refreshBadge()
        } catch { self.error = error.localizedDescription }
    }

    private func load() async {
        guard let api = model.api else { return }
        let request = UUID()
        loadID = request
        loading = true
        error = nil
        defer { if loadID == request { loading = false } }
        do {
            var loaded: [Txn] = []
            var firstPage: TxnPage?
            while true {
                let page: TxnPage = try await api.get("api/transactions", [
                    "month": detail.month.isEmpty ? nil : detail.month, "category": detail.category, "account": detail.account, "status": detail.status,
                    "end_date": detail.through, "currency": detail.currency, "cashflow_only": detail.cashflowOnly ? "true" : nil,
                    "include_summary": firstPage == nil ? "true" : nil, "limit": "2000", "offset": String(loaded.count),
                ])
                try Task.checkCancellation()
                if firstPage == nil { firstPage = page }
                loaded.append(contentsOf: page.items)
                if loaded.count >= page.total || page.items.isEmpty { break }
            }
            let choices = (try? await api.categories()) ?? []
            guard !Task.isCancelled, loadID == request else { return }
            items = loaded
            summary = firstPage
            categories = choices
        } catch is CancellationError {
        } catch {
            if !Task.isCancelled, loadID == request { self.error = error.localizedDescription }
        }
    }
}
