import KeelCore
import SwiftUI

/// The window: Keel's top bar in the unified toolbar (brand, 现在 · 回顾 · 账本 · 规划, settings,
/// 拖入账单) over the current place, on chart paper. Files can be dropped anywhere.
struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        content
            .id(model.lang)  // language switch: rebuild every screen in the new language
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Theme.paper)
            .navigationTitle("")
            .toolbar { TopBar(model: model) }
            .toolbarBackground(Theme.paper, for: .windowToolbar)
            .toolbarBackground(.visible, for: .windowToolbar)
            .dropDestination(for: URL.self) { urls, _ in
                model.importFiles(urls.filter(\.isFileURL))
                return true
            }
            .overlay(alignment: .bottomTrailing) { Banner() }
            .background(ToolbarSpacing())
            .onChange(of: model.revision) { _, _ in Task { await model.loadRange() } }
            .sheet(isPresented: Binding(get: { model.showPendingFiles }, set: { model.showPendingFiles = $0 })) {
                PendingFilesSheet().environment(model)
            }
            .sheet(isPresented: Binding(get: { model.showGuide }, set: { model.showGuide = $0 })) {
                DownloadGuide().environment(model)
            }
            .sheet(item: Binding(get: { model.transactionDetail }, set: { model.transactionDetail = $0 })) { detail in
                TransactionDetailView(detail: detail).environment(model)
            }
    }

    @ViewBuilder private var content: some View {
        switch model.engine.state {
        case .starting:
            VStack(spacing: 14) {
                KeelMark(size: 44)
                Text(L("正在打开账本…")).font(.ui(13)).foregroundStyle(Theme.muted)
            }
        case let .failed(message):
            VStack(spacing: 12) {
                Text(L("账本没能打开")).font(.display(24)).foregroundStyle(Theme.ink)
                Text(message).font(.ui(13)).foregroundStyle(Theme.muted)
                    .multilineTextAlignment(.center).textSelection(.enabled).frame(maxWidth: 480)
                Button(L("重试")) { model.engine.retry() }.buttonStyle(.keelPrimary)
            }
            .padding(40)
        case .ready:
            switch model.place {
            case .now, .report: MonthlyView()
            case .details: DetailsView()
            case .plan: PlanView()
            case .settings: SettingsView()
            }
        }
    }
}

/// The web top bar, natively: mark + name, the three places, then settings and import.
struct TopBar: ToolbarContent {
    let model: AppModel

    var body: some ToolbarContent {
        let _ = model.lang  // relabel when the language changes
        ToolbarItem(placement: .navigation) {
            HStack(spacing: 22) {
                Button { model.open(.now) } label: {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        KeelMark(size: 24).alignmentGuide(.firstTextBaseline) { $0[.bottom] - 6 }
                        Text(verbatim: "Keel").font(.display(18)).foregroundStyle(Theme.ink)
                        Text(verbatim: "龙骨").font(.ui(10.5)).foregroundStyle(Theme.muted)  // l10n-allow: part of the name
                    }
                }
                .buttonStyle(.plain)
                .help(L("Keel 首页"))
                PlaceTabs()
            }
            .padding(.leading, 6)
        }
        ToolbarItem(placement: .automatic) {
            HStack(spacing: 8) {
                if model.busy { ProgressView().controlSize(.small).padding(.trailing, 4) }
                TodosButton()
                Button { model.open(.settings) } label: {
                    Image(systemName: "gearshape")
                        .font(.ui(13))
                        .frame(width: 16, height: 16)
                }
                .buttonStyle(.keelOutlineCompact)
                .help(L("设置"))
                Button(L("拖入账单")) { model.chooseFiles() }
                    .buttonStyle(.keelOutlineCompact)
                    .help(L("也可以直接把文件拖进窗口任何地方"))
            }
            .fixedSize()  // never truncate 拖入账单 when 待办 joins
            .padding(.trailing, 6)
        }
    }
}

private struct PlaceTabs: View {
    @Environment(AppModel.self) private var model

    private enum Top: Hashable { case now, calendar, bills, holdings, plan }

    private var current: Top {
        switch model.place {
        case .plan: return .plan
        case .details where model.detailTab == .calendar: return .calendar
        case .details where model.detailTab == .holdings: return .holdings
        case .details where model.detailTab == .bills: return .bills
        default: return .now  // 现在, 回顾 and the detail pages opened from them
        }
    }

    var body: some View {
        // 每月: the month in progress (现在) and, stepping back, the closed months (回顾).
        // 持仓 appears once a connected broker has reported positions or trades.
        let items: [(Top, String)] = [(.now, L("每月")), (.calendar, L("日历")), (.bills, L("账单"))]
            + (model.hasHoldings ? [(.holdings, L("持仓"))] : [])
            + [(.plan, L("规划"))]
        UnderlineTabs(
            items: items,
            selection: Binding(get: { current }, set: { top in
                switch top {
                case .now: model.open(model.reportMonth == nil ? .now : .report)
                case .calendar: model.open(.details, tab: .calendar)
                case .holdings: model.open(.details, tab: .holdings)
                case .bills: model.open(.details, tab: .bills)
                case .plan: model.open(.plan)
                }
            }),
            size: 13,
            spacing: 18
        )
        .padding(.top, 5)
    }
}

/// 明细: 花销 · 日历 · 资产 · 交易, with the report month on the right for 花销 and 资产.
struct DetailsView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Page {
            VStack(alignment: .leading, spacing: 32) {
                if ![.calendar, .holdings, .bills].contains(model.detailTab) {
                    LinkButton(title: L("每月")) { model.open(.now) }
                }
                switch model.detailTab {
                case .spending: SpendingView()
                case .calendar: CalendarView()
                case .assets: AssetsView()
                case .holdings: HoldingsView()
                case .bills: BillsView()
                case .transactions: TransactionsView()
                }
            }
        }
    }
}

struct DetailTabs: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .bottom) {
                UnderlineTabs(
                    items: DetailTab.allCases.map { ($0, $0.title) },
                    selection: Binding(get: { model.detailTab }, set: { model.open(.details, tab: $0) }),
                    size: 16
                )
                Spacer()
                if model.detailTab.hasMonth, let month = model.month, let latest = model.currentMonth {
                    MonthStepper(month: Binding(get: { month }, set: { model.month = $0 }), latest: latest)
                }
            }
            .frame(minHeight: 34, alignment: .bottom)
            Hairline()
        }
    }
}

/// A short status note in the corner (import results, errors); fades after a while.
struct Banner: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        if let text = model.banner {
            Text(text)
                .font(.ui(13))
                .foregroundStyle(Theme.ink)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(Theme.card, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(Theme.border))
                .shadow(color: .black.opacity(0.08), radius: 12, y: 4)
                .padding(20)
                .transition(.move(edge: .bottom).combined(with: .opacity))
                .task(id: text) {
                    try? await Task.sleep(for: .seconds(model.busy ? 30 : 6))
                    if model.banner == text { withAnimation { model.banner = nil } }
                }
        }
    }
}

/// SwiftUI packs toolbar items to the leading edge when the window shows no title; a flexible
/// space after the navigation item pushes settings and 拖入账单 to the trailing edge, as on the web.
private struct ToolbarSpacing: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async { Self.insert(into: view.window) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { Self.insert(into: view.window) }
        return view
    }

    func updateNSView(_ view: NSView, context: Context) {
        DispatchQueue.main.async { Self.insert(into: view.window) }
    }

    @MainActor private static func insert(into window: NSWindow?) {
        guard let toolbar = window?.toolbar, toolbar.items.count >= 2,
              !toolbar.items.contains(where: { $0.itemIdentifier == .flexibleSpace })
        else { return }
        toolbar.insertItem(withItemIdentifier: .flexibleSpace, at: 1)
    }
}


/// 待办 N: what keeps the books right (files waiting, lines to categorise, accounts behind or
/// never checked, balances that disagree). Shown only when there is something; the monthly page
/// stays about spending.
struct TodosButton: View {
    @Environment(AppModel.self) private var model
    @State private var todos: [Todo] = []
    @State private var showing = false

    private static let kinds: Set<String> = ["files", "review", "stale", "balance", "reconcile"]

    var body: some View {
        // An always-present anchor: an empty Group never appears, so its task would never run.
        HStack(spacing: 0) {
            if !todos.isEmpty {
                Button(L("待办 {n}", ["n": todos.count])) { showing = true }
                    .buttonStyle(.keelOutlineCompact)
                    .help(L("让账保持准确要做的事"))
            }
        }
        .frame(minWidth: 1)
        .task(id: model.revision) {
            guard let api = model.api, let home = try? await api.home() else { return }
            todos = home.todos.filter { Self.kinds.contains($0.kind) }
        }
        .sheet(isPresented: $showing) {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    Text(L("待办")).font(.display(24)).foregroundStyle(Theme.ink)
                    Spacer()
                    Button(L("完成")) { showing = false }.buttonStyle(.keelOutlineCompact).keyboardShortcut(.cancelAction)
                }
                TodosCard(todos: todos, more: 0)
                    .simultaneousGesture(TapGesture().onEnded { showing = false })
            }
            .padding(24)
            .frame(width: 620)
            .background(Theme.paper)
            .environment(model)
        }
    }
}
