import Foundation

enum AccountRole: String, Codable, CaseIterable, Identifiable {
    case admin, user, guest
    var id: String { rawValue }
    var title: String {
        switch self {
        case .admin: "Administrator"
        case .user: "Benutzer"
        case .guest: "Gast"
        }
    }
}

enum AccountPasswordPolicy {
    static func accepts(_ password: String) -> Bool {
        password.unicodeScalars.count >= 10 && password.utf8.count <= 72
    }
    static let guidance = "Mindestens 10 Zeichen, höchstens 72 UTF-8-Bytes."
}

struct AccountProfile: Decodable {
    let id: Int
    let username: String
    let role: AccountRole
    let createdAt: Double
    let lastLoginAt: Double?
    let passwordEnabled: Bool
}

struct AccountSessions: Decodable { let sessions: [AccountSession] }

struct AccountSession: Decodable, Identifiable {
    let id: String
    let createdAt: Double
    let lastSeenAt: Double?
    let expiresAt: Double
    let clientLabel: String
    let isCurrent: Bool
}

struct AdminUsers: Decodable { let users: [AdminUser] }

struct AdminUser: Decodable, Identifiable {
    let id: Int
    let username: String
    let role: AccountRole
    @FlexibleBool var disabled: Bool?
    let createdAt: Double
    let lastLoginAt: Double?
}

struct AdminUserPatch: Encodable {
    var password: String?
    var disabled: Bool?
    var role: AccountRole?
}

enum IdentityProvider: String, Codable, CaseIterable, Identifiable {
    case apple, google
    var id: String { rawValue }
    var title: String { self == .apple ? "Apple" : "Google" }
}

struct AuthProvider: Decodable, Identifiable {
    let id: String
    let name: String
    let enabled: Bool
    var kind: IdentityProvider? { IdentityProvider(rawValue: id) }
}

struct AuthProviders: Decodable {
    let providers: [AuthProvider]
    var available: [AuthProvider] { providers.filter { $0.enabled && $0.kind != nil } }
}

struct AccountIdentity: Decodable, Identifiable {
    let provider: String
    let email: String?
    let linkedAt: Double
    var id: String { provider }
    var title: String { IdentityProvider(rawValue: provider)?.title ?? provider }
}

struct AccountIdentities: Decodable {
    let identities: [AccountIdentity]
    let providers: [AuthProvider]
}

struct NativeAuthStart: Decodable {
    let authorizationUrl: String
    let flowId: String
}

enum NativeAuthIntent: String, Encodable { case login, link }

struct NativeAuthStartPayload: Encodable {
    let platform = "native"
    let intent: NativeAuthIntent
    let codeChallenge: String
    let invitationToken: String?
    let currentPassword: String?
}

struct NativeAuthExchangePayload: Encodable {
    let code: String
    let codeVerifier: String
}
