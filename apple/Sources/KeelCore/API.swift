import Foundation

public struct APIError: LocalizedError, Sendable {
    public let status: Int
    public let message: String
    public var errorDescription: String? { message }
    public init(status: Int, message: String) {
        self.status = status
        self.message = message
    }
}

/// HTTP client for the local engine. Every request carries the UI language, so text the engine
/// writes (insights, to-dos, alerts, errors) comes back in the same language as the screens.
public struct API: Sendable {
    public let base: URL
    public let language: String  // zh | en

    public init(base: URL, language: String) {
        self.base = base
        self.language = language
    }

    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()

    func url(_ path: String, _ query: [String: String?]) -> URL {
        var c = URLComponents(url: base.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        let items = query.compactMap { k, v in v.map { URLQueryItem(name: k, value: $0) } }
        c.queryItems = items.isEmpty ? nil : items.sorted { $0.name < $1.name }
        return c.url!
    }

    func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        var request = request
        request.setValue(language, forHTTPHeaderField: "Accept-Language")
        request.timeoutInterval = 120
        let (data, response) = try await URLSession.shared.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"]
            let fields = (detail as? [[String: Any]])?.compactMap { $0["msg"] as? String }.joined(separator: "; ")
            let message = (detail as? String) ?? fields ?? String(decoding: data.prefix(300), as: UTF8.self)
            throw APIError(status: status, message: message)
        }
        return try API.decoder.decode(T.self, from: data)
    }

    public func get<T: Decodable>(_ path: String, _ query: [String: String?] = [:]) async throws -> T {
        try await send(URLRequest(url: url(path, query)))
    }

    public func post<T: Decodable, B: Encodable>(_ path: String, _ body: B) async throws -> T {
        try await send(json("POST", path, body))
    }

    public func put<T: Decodable, B: Encodable>(_ path: String, _ body: B) async throws -> T {
        try await send(json("PUT", path, body))
    }

    public func delete<T: Decodable>(_ path: String, _ query: [String: String?] = [:]) async throws -> T {
        var r = URLRequest(url: url(path, query))
        r.httpMethod = "DELETE"
        return try await send(r)
    }

    func json<B: Encodable>(_ method: String, _ path: String, _ body: B) throws -> URLRequest {
        var r = URLRequest(url: url(path, [:]))
        r.httpMethod = method
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try API.encoder.encode(body)
        return r
    }

    /// POST /api/import with the files as multipart form data (field "files").
    public func upload(_ files: [URL]) async throws -> [ImportResult] {
        let boundary = "keel-\(UUID().uuidString)"
        var body = Data()
        for file in files {
            let data = try Data(contentsOf: file)
            let name = file.lastPathComponent.replacingOccurrences(of: "\"", with: "_")
            body.append(Data("--\(boundary)\r\n".utf8))
            body.append(Data("Content-Disposition: form-data; name=\"files\"; filename=\"\(name)\"\r\n".utf8))
            body.append(Data("Content-Type: application/octet-stream\r\n\r\n".utf8))
            body.append(data)
            body.append(Data("\r\n".utf8))
        }
        body.append(Data("--\(boundary)--\r\n".utf8))
        var r = URLRequest(url: url("api/import", [:]))
        r.httpMethod = "POST"
        r.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        r.httpBody = body
        return try await send(r)
    }

    // MARK: typed endpoints

    public func home(month: String? = nil) async throws -> Home { try await get("api/home", ["month": month]) }
    public func networth(start: String? = nil, end: String, currency: String = "USD") async throws -> [NWPoint] { try await get("api/reports/networth", ["start": start, "end": end, "currency": currency]) }
    public func pulse(currency: String = "USD") async throws -> Pulse {
        try await get("api/pulse", ["currency": currency])
    }
    /// Dates are yyyy-MM-dd, both ends included.
    public func cashline(start: String, end: String, currency: String? = nil) async throws -> [CashFlowDay] {
        try await get("api/cashline", ["start": start, "end": end, "currency": currency])
    }
    public func overview(month: String, currency: String? = nil) async throws -> Overview {
        try await get("api/reports/overview", ["month": month, "currency": currency])
    }
    public func transactions(q: String? = nil, status: String? = nil, limit: Int = 300) async throws -> TxnPage {
        try await get("api/transactions", ["q": q, "status": status, "limit": String(limit)])
    }
    /// Every line of one month (日历 sums each day's spending and income from them).
    public func transactions(month: String, currency: String? = nil, limit: Int = 2000) async throws -> TxnPage {
        try await get("api/transactions", ["month": month, "currency": currency, "limit": String(limit)])
    }
    public func categories() async throws -> [String] { try await get("api/categories") }
    public func setCategory(_ txn: Int, _ category: String) async throws -> CategorizeOut {
        try await post("api/transactions/\(txn)/category", CategorizeIn(category: category))
    }
    public func addTransaction(_ body: ManualIn) async throws -> Txn {
        try await post("api/transactions", body)
    }
    public func editTransaction(_ id: Int, _ body: EditIn) async throws -> Txn {
        try await send(json("PATCH", "api/transactions/\(id)", body))
    }
    /// Empty `parts` undoes the split.
    public func splitTransaction(_ id: Int, _ parts: [TxnPart]) async throws -> Txn {
        try await post("api/transactions/\(id)/split", SplitIn(parts: parts))
    }
    public func deleteTransaction(_ id: Int) async throws {
        let _: [String: Bool] = try await delete("api/transactions/\(id)")
    }
    public func confirm(_ ids: [Int]) async throws {
        let _: [String: Int] = try await post("api/transactions/confirm", IdsIn(ids: ids))
    }
    public func scanInbox() async throws -> ScanResult {
        try await post("api/inbox/scan", Empty())
    }
    public func prefs() async throws -> Prefs { try await get("api/settings") }
    public func ownerNames() async throws -> [OwnerName] { try await get("api/owner-names") }
    public func addOwnerName(_ name: String) async throws -> [OwnerName] {
        try await post("api/owner-names", OwnerName(name: name))
    }
    public func deleteOwnerName(_ name: String) async throws -> [OwnerName] {
        try await delete("api/owner-names", ["name": name])
    }
    public func savePrefs(_ p: Prefs) async throws -> Prefs { try await put("api/settings", p) }
    public func backupStatus() async throws -> BackupStatus { try await get("api/backup") }
    public func backupNow() async throws -> BackupStatus { try await post("api/backup", Empty()) }
    /// A token that changes whenever the ledger does, whoever changed it (background sync, the
    /// `keel` command, a statement dropped in Downloads). See AppModel.watchLedger.
    public func ledgerRevision() async throws -> String {
        let r: [String: String] = try await get("api/revision")
        return r["revision"] ?? ""
    }
    // Recurring bills and subscriptions.
    public func bills(currency: String = "USD") async throws -> Bills {
        try await get("api/bills", ["currency": currency])
    }
    public func dismissBill(_ key: String, name: String, currency: String = "USD") async throws -> Bills {
        var request = try json("POST", "api/bills/dismiss", ["key": key, "name": name])
        request.url = url("api/bills/dismiss", ["currency": currency])
        return try await send(request)
    }
    public func restoreBill(_ key: String, currency: String = "USD") async throws -> Bills {
        try await delete("api/bills/dismiss", ["key": key, "currency": currency])
    }

    // Brokerage positions, trades and gains.
    /// `live`: valued at delayed market quotes instead of the broker report's prices.
    public func holdings(currency: String = "USD", live: Bool = false) async throws -> Holdings {
        try await get("api/portfolio", ["currency": currency, "live": live ? "true" : nil])
    }
    public func portfolioTrades(limit: Int = 200) async throws -> [PortfolioTrade] {
        try await get("api/portfolio/trades", ["limit": String(limit)])
    }
    public func realizedGains(currency: String = "USD") async throws -> RealizedGains {
        try await get("api/portfolio/realized", ["currency": currency])
    }
    public func performance(currency: String = "USD") async throws -> Performance {
        try await get("api/portfolio/performance", ["currency": currency])
    }
    public func symbolHistory(_ symbol: String) async throws -> SymbolHistory {
        try await get("api/portfolio/history", ["symbol": symbol])
    }
    /// Accounts, positions at `end` and trades from `start` to `end`, as one CSV file's text.
    public func brokerageStatementCSV(start: String, end: String, currency: String = "USD") async throws -> String {
        var request = URLRequest(url: url("api/portfolio/statement.csv", ["start": start, "end": end, "currency": currency]))
        request.setValue(language, forHTTPHeaderField: "Accept-Language")
        let (data, response) = try await URLSession.shared.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            throw APIError(status: status, message: String(decoding: data.prefix(300), as: UTF8.self))
        }
        return String(decoding: data, as: UTF8.self)
    }
    public func version() async throws -> VersionInfo { try await get("api/app/version") }
    public func checkUpdate() async throws -> UpdateCheck { try await post("api/app/update/check", Empty()) }
    public func applyUpdate() async throws -> [String: String] {
        try await post("api/app/update/apply", Empty())
    }

    // MARK: review by merchant or person
    public func reviewGroups() async throws -> [ReviewGroup] { try await get("api/review/groups") }
    public func applyGroup(_ g: ReviewGroup, category: String) async throws -> OkCount {
        try await post("api/review/groups/apply", GroupDecision(txnIds: g.txnIds, category: category, remember: true))
    }

    // MARK: account connections (Settings → 连接)
    public func connections() async throws -> [AccountConnection] { try await get("api/connections") }
    public func saveConnection(_ source: String, _ values: [String: String]) async throws -> AccountConnection {
        try await put("api/connections/\(source)", ConnectionKeys(values: values))
    }
    public func disconnect(_ source: String) async throws -> AccountConnection {
        try await delete("api/connections/\(source)")
    }
    public func syncConnection(_ source: String) async throws -> SyncOutcome {
        try await post("api/connections/\(source)/sync", Empty())
    }
    public func plaidLink() async throws -> LinkURL { try await post("api/connections/plaid/link", Empty()) }
    public func plaidLinkFinish(_ token: String) async throws -> LinkedBanks {
        try await post("api/connections/plaid/link/finish", LinkToken(linkToken: token))
    }
    public func snaptradePortal() async throws -> LinkURL {
        try await post("api/connections/snaptrade/portal", Empty())
    }
}
