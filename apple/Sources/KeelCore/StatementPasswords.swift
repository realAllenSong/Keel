import Foundation

public struct StatementPasswordStatus: Decodable, Sendable, Identifiable {
    public let provider: String
    public let configured: Bool
    public let count: Int
    public let updatedAt: String?
    public var id: String { provider }
}

public struct StatementPasswordResult: Decodable, Sendable {
    public let unlocked: Int
    public let newTransactions: Int
    public let remaining: Int
    public let errors: Int
    public let passwords: [StatementPasswordStatus]
}

private struct StatementPasswordInput: Encodable, Sendable { let password: String }

extension API {
    public func statementPasswords() async throws -> [StatementPasswordStatus] { try await get("api/onboarding/statement-passwords") }
    public func saveStatementPassword(_ provider: String, password: String) async throws -> StatementPasswordResult {
        try await put("api/onboarding/statement-passwords/\(provider)", StatementPasswordInput(password: password))
    }
    public func clearStatementPasswords(_ provider: String) async throws -> [StatementPasswordStatus] {
        try await delete("api/onboarding/statement-passwords/\(provider)")
    }
}
