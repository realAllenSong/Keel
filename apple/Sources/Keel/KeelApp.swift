import AppKit
import KeelCore
import OSLog
import ServiceManagement
import SwiftUI

@main
struct KeelApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var model = AppModel.shared

    var body: some Scene {
        Window("Keel", id: "main") {
            RootView()
                .environment(model)
                .frame(minWidth: 1000, minHeight: 660)
        }
        .defaultSize(width: 1320, height: 900)
        .windowToolbarStyle(.unified(showsTitle: false))
        .commands {
            CommandGroup(replacing: .appSettings) {
                Button(L("设置…")) { model.place = .settings }
                    .keyboardShortcut(",", modifiers: .command)
            }
            CommandGroup(after: .newItem) {
                Button(L("导入账单…")) { model.chooseFiles() }
                    .keyboardShortcut("o", modifiers: .command)
            }
        }

        MenuBarExtra {
            MenuBarMenu().environment(model)
        } label: {
            Image(systemName: model.pendingCount > 0 ? "sailboat.fill" : "sailboat")
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationWillFinishLaunching(_ notification: Notification) {
        // Snapshots taken in the background: no Dock icon, no focus taken from your work.
        if Snapshot.offscreen { NSApp.setActivationPolicy(.accessory) }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        MainActor.assumeIsolated { AppModel.shared.launch() }
    }

    // Closing the window keeps Keel in the menu bar (imports and alerts keep running).
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { AppModel.shared.engine.stop() }
    }
}

/// 本月 (the month in progress), 月报 (a closed month), 明细 with four tabs, 规划, plus settings.
enum Place: Hashable {
    case now, report, details, plan, settings
}

enum DetailTab: String, CaseIterable {
    case spending, calendar, assets, holdings, bills, transactions

    var title: String {
        switch self {
        case .spending: return L("收支")
        case .calendar: return L("日历")
        case .assets: return L("资产")
        case .holdings: return L("持仓")
        case .bills: return L("固定收支")
        case .transactions: return L("交易")
        }
    }

    /// 花销 and 资产 report on one month, chosen with the stepper next to the tabs.
    var hasMonth: Bool { self == .spending || self == .assets }
}

/// 规划 is the planning workspace; its two tools open as pages under it.
enum PlanTool: Hashable {
    case workspace, backtest, housing
}

@MainActor
@Observable
final class AppModel {
    static let shared = AppModel()

    let engine = Engine()
    var place: Place = .now
    var detailTab: DetailTab = .spending
    var planTool: PlanTool = .workspace
    /// 交易 opens on these filters (the menu bar opens it on 待确认; a category in 花销 opens it on
    /// that category and month).
    var txnStatus = ""
    var txnMonth: String?
    var txnCategory: String?
    var txnAccount: String?
    var transactionDetail: TransactionDetail?
    /// The month 花销 and 资产 report on (the month in progress by default); nil until the
    /// ledger's range is known.
    var month: String?
    /// The newest month in the ledger: the one in progress.
    var currentMonth: String?
    /// The closed month 月报 shows; nil means the newest one.
    var reportMonth: String?
    var latestMonth: String?
    var firstMonth: String?
    /// The newest day with a ledger line: where the imported statements end.
    var lastDay: String?
    var showAnnual = false
    var summaryYear = Int(ReportingPeriod.month().prefix(4)) ?? 2026
    private var clockDay = ""
    var today: String { ReportingPeriod.day() }
    /// Statements waiting for an account, a password or a column mapping.
    var showPendingFiles = false
    var pendingFileCount = 0
    /// A connected broker has reported positions or trades: the 持仓 page has something to show.
    var hasHoldings = UserDefaults.standard.bool(forKey: "keel.hasHoldings")
    var showGuide = false
    var lang: Lang = L10n.current
    var theme: String = UserDefaults.standard.string(forKey: "keel.theme") ?? "system"
    /// Bumped after anything changes the ledger (imports, categorising): screens reload.
    var revision = 0
    /// The engine's ledger token last seen (api.ledgerRevision), to notice changes made elsewhere.
    @ObservationIgnored private var ledgerToken: String?
    var pendingCount = 0
    var banner: String?
    var busy = false

    var api: API? { engine.baseURL.map { API(base: $0, language: lang.rawValue) } }

    func launch() {
        refreshClock()
        applyTheme()
        engine.start()
        if let dir = Snapshot.directory {
            Task { await Snapshot.run(self, into: dir) }
        }
        Task {
            await self.loadRange()
            await self.refreshBadge()
        }
        Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { _ in
            Task { @MainActor in
                AppModel.shared.refreshClock()
                await AppModel.shared.refreshBadge()
            }
        }
        Timer.scheduledTimer(withTimeInterval: 4, repeats: true) { _ in
            Task { @MainActor in await AppModel.shared.watchLedger() }
        }
    }

    private static let log = Logger(subsystem: "com.allensong.keel", category: "ledger")

    /// Reloads every screen when the ledger changed behind the app's back: the 7:30 sync (in the
    /// engine or the background agent), the `keel` command, a statement arriving in Downloads.
    /// Asks only while a Keel window is open (not when Keel is just in the menu bar); a window
    /// opened later catches up on its first tick. The app's own edits move the token too, which costs one extra quiet reload (screens
    /// keep their content while they reload).
    func watchLedger() async {
        guard NSApp.windows.contains(where: { $0.canBecomeMain && $0.isVisible }),
              let api, let token = try? await api.ledgerRevision(), !token.isEmpty
        else { return }
        defer { ledgerToken = token }
        guard let seen = ledgerToken, seen != token else { return }
        Self.log.info("Ledger changed (\(seen, privacy: .public) -> \(token, privacy: .public)); reloading")
        revision += 1
        await refreshBadge()
    }

    // MARK: preferences

    func setLanguage(_ preference: String) {
        L10n.preference = preference
        lang = L10n.resolve(preference)
        Task { await pushPrefs { $0.language = preference } }
    }

    func setTheme(_ value: String) {
        theme = value
        UserDefaults.standard.set(value, forKey: "keel.theme")
        applyTheme()
        Task { await pushPrefs { $0.theme = value } }
    }

    func applyTheme() {
        switch theme {
        case "light": NSApp.appearance = NSAppearance(named: .aqua)
        case "dark": NSApp.appearance = NSAppearance(named: .darkAqua)
        default: NSApp.appearance = nil
        }
    }

    /// The engine keeps a copy for notifications and the daily job.
    private func pushPrefs(_ change: (inout Prefs) -> Void) async {
        guard let api, var p = try? await api.prefs() else { return }
        change(&p)
        _ = try? await api.savePrefs(p)
    }

    // MARK: importing

    func chooseFiles() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.commaSeparatedText, .plainText, .zip, .spreadsheet, .data]
        panel.message = L("选择银行、券商、支付宝或微信导出的账单")
        if panel.runModal() == .OK { importFiles(panel.urls) }
    }

    func importFiles(_ urls: [URL]) {
        guard let api, !urls.isEmpty else { return }
        busy = true
        banner = L("正在入账…")
        Task {
            defer { busy = false }
            do {
                let results = try await api.upload(urls)
                banner = summary(results)
                revision += 1
                await refreshBadge()
                if results.contains(where: { ["needs_account", "unknown_format", "needs_password"].contains($0.status) }) {
                    showPendingFiles = true
                }
            } catch {
                banner = L("导入失败：{e}", ["e": error.localizedDescription])
            }
        }
    }

    func scanDownloads() {
        guard let api else { return }
        banner = L("正在检查下载文件夹…")
        Task {
            do {
                let r = try await api.scanInbox()
                let imported = r.outcomes.filter { $0.status == "imported" }.count
                banner = imported > 0
                    ? L("从下载文件夹导入了 {n} 份账单", ["n": imported])
                    : L("下载文件夹里没有新账单")
                revision += 1
                await refreshBadge()
            } catch {
                banner = L("检查失败：{e}", ["e": error.localizedDescription])
            }
        }
    }

    private func summary(_ results: [ImportResult]) -> String {
        var parts: [String] = []
        let imported = results.filter { $0.status == "imported" }
        if !imported.isEmpty {
            parts.append(L("入账 {n} 笔", ["n": imported.reduce(0) { $0 + ($1.new ?? 0) }]))
        }
        let pending = results.filter { ["needs_account", "unknown_format", "needs_password"].contains($0.status) }.count
        if pending > 0 { parts.append(L("{n} 份需要你确认", ["n": pending])) }
        let dupes = results.filter { $0.status == "already_imported" }.count
        if dupes > 0 { parts.append(L("{n} 个之前导入过", ["n": dupes])) }
        return parts.isEmpty ? L("没有可导入的内容") : parts.joined(separator: L("，"))
    }

    /// 月报 on `month` (the newest closed month when nil).
    func openReport(_ month: String? = nil) {
        selectSummaryMonth(month ?? Self.shift(ReportingPeriod.month(), -1))
    }

    func selectSummaryMonth(_ selected: String) {
        refreshClock()
        month = selected
        showAnnual = false
        if selected < (currentMonth ?? ReportingPeriod.month()) {
            reportMonth = selected
            place = .report
        } else { reportMonth = nil; place = .now }
    }

    func refreshClock() {
        let current = ReportingPeriod.month()
        let old = currentMonth
        currentMonth = current
        latestMonth = Self.shift(current, -1)
        if month == nil || month == old { month = current }
        if !clockDay.isEmpty, clockDay != today { revision += 1 }
        clockDay = today
    }

    func open(_ place: Place, tab: DetailTab? = nil) {
        if let tab { detailTab = tab }
        self.place = place
    }

    /// Transactions waiting for a decision (and statements that need an account).
    func showPending() {
        showTransactions(status: "pending")
    }

    /// Opens 交易 filtered to one category / account / month (links from 花销, 资产, insights).
    func showTransactions(month: String? = nil, category: String? = nil, account: String? = nil, status: String = "") {
        txnMonth = month
        txnCategory = category
        txnAccount = account
        txnStatus = status
        transactionDetail = TransactionDetail(month: month ?? "", category: category, account: account, status: status.isEmpty ? nil : status)
    }

    /// Records a finished month as closed (the engine refuses one that isn't complete).
    func closeMonth(_ month: String) {
        guard let api else { return }
        Task {
            do {
                _ = try await api.closeMonth(month)
                banner = L("{m} 已结账", ["m": Format.month(month)])
                revision += 1
            } catch {
                banner = L("结账失败：{e}", ["e": error.localizedDescription])
            }
        }
    }

    /// Follows an engine link (to-dos, alerts and insights carry web-style hrefs).
    @discardableResult
    func route(_ href: String) -> Bool {
        if href == "#pending" || href.hasSuffix("#pending") {
            showPendingFiles = true
            return true
        }
        if href.hasPrefix("#close"), let month = URLComponents(string: href)?.queryItems?.first(where: { $0.name == "month" })?.value {
            closeMonth(month)
            return true
        }
        guard let url = URL(string: href, relativeTo: URL(string: "http://keel.local")) else { return false }
        let items = URLComponents(url: url.absoluteURL, resolvingAgainstBaseURL: false)?.queryItems ?? []
        func q(_ name: String) -> String? { items.first { $0.name == name }?.value.flatMap { $0.isEmpty ? nil : $0 } }
        switch url.path {
        case "/", "": open(.now)
        case "/report", "/review": openReport(q("month"))
        case "/settings": open(.settings)
        case "/plan/backtest": planTool = .backtest; open(.plan)
        case "/plan/home", "/calculators": planTool = .housing; open(.plan)
        case let p where p.hasPrefix("/plan") || p.hasPrefix("/sim") || p.hasPrefix("/lighthouses"):
            planTool = .workspace
            open(.plan)
        case "/details", "/transactions":
            let tab = q("tab").flatMap(DetailTab.init(rawValue:)) ?? (url.path == "/transactions" ? .transactions : .spending)
            if let m = q("month"), tab.hasMonth { month = m }
            if tab == .transactions {
                showTransactions(month: q("month"), category: q("category"), account: q("account"), status: q("status") ?? "")
            } else {
                open(.details, tab: tab)
            }
        default: return false
        }
        return true
    }

    /// The month in progress (花销, 资产, 日历 open on it) and the last closed one (回顾's newest).
    func loadRange() async {
        while api == nil { try? await Task.sleep(for: .milliseconds(200)) }
        guard let api, let range = try? await api.range() else { return }
        // The month on the user's own clock: at 10 pm on Oct 31 in New York it is still October
        // (UTC is already November).
        firstMonth = range.first.map { String($0.prefix(7)) }
        lastDay = range.last
        refreshClock()
    }

    static func shift(_ ym: String, _ delta: Int) -> String {
        let p = ym.split(separator: "-").compactMap { Int($0) }
        guard p.count == 2 else { return ym }
        let total = p[0] * 12 + (p[1] - 1) + delta
        return String(format: "%04d-%02d", total / 12, total % 12 + 1)
    }

    func refreshBadge() async {
        guard let api, let page = try? await api.transactions(status: "pending", limit: 1) else { return }
        pendingCount = page.total
        pendingFileCount = (try? await api.pending().count) ?? pendingFileCount
        if let h = try? await api.holdings() {
            var held = h.accounts.contains { !$0.positions.isEmpty }
            if !held { held = !((try? await api.portfolioTrades(limit: 1)) ?? []).isEmpty }
            hasHoldings = held
            UserDefaults.standard.set(held, forKey: "keel.hasHoldings")  // no flicker at next launch
        }
    }

    // MARK: launch at login (needs the app bundle)

    var launchAtLogin: Bool {
        get { SMAppService.mainApp.status == .enabled }
        set {
            do {
                if newValue { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            } catch {
                banner = error.localizedDescription
            }
        }
    }
}

struct MenuBarMenu: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button(L("打开 Keel")) { show(.now) }
        Button(model.pendingCount > 0 ? L("{n} 笔交易待确认", ["n": model.pendingCount]) : L("没有待确认的交易")) {
            model.showPending()
            show(.now)
        }
        .disabled(model.pendingCount == 0)
        if model.pendingFileCount > 0 {
            Button(L("{n} 份账单等你确认", ["n": model.pendingFileCount])) {
                show(.now)
                model.showPendingFiles = true
            }
        }
        Divider()
        Button(L("导入账单…")) {
            openWindow(id: "main")
            NSApp.activate(ignoringOtherApps: true)
            model.chooseFiles()
        }
        Button(L("检查下载文件夹")) { model.scanDownloads() }
        Button(L("检查更新…")) { show(.settings) }
        Divider()
        Button(L("退出 Keel")) { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }

    private func show(_ place: Place, tab: DetailTab? = nil) {
        model.open(place, tab: tab)
        openWindow(id: "main")
        NSApp.activate(ignoringOtherApps: true)
    }
}
