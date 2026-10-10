import Foundation
import XCTest
@testable import Rezepte

final class AccountManagementTests: XCTestCase {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: Data(json.utf8))
    }

    private func client() async throws -> APIClient {
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de/rezepte", token: "account-token")
        return client
    }

    private func body() throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Any])
    }

    func testExplicitRolesTakePrecedenceOverLegacyAccessFlags() throws {
        let user = try decode(SessionResponse.self, #"{"id":12,"username":"member","role":"user","is_admin":false,"full_access":true,"read_only":false,"password_enabled":false}"#)
        XCTAssertEqual(user.effectiveRole, .user)
        XCTAssertEqual(user.id, 12)
        XCTAssertEqual(user.passwordEnabled, false)
        let legacy = try decode(SessionResponse.self, #"{"username":"admin","full_access":true,"read_only":false}"#)
        XCTAssertEqual(legacy.effectiveRole, .user, "Legacy full_access is not a role assignment")
        let guest = try decode(SessionResponse.self, #"{"username":"Gast","full_access":false,"read_only":true}"#)
        XCTAssertEqual(guest.effectiveRole, .guest)
    }

    func testAllFourRolesDecodeAndSerializeForUserManagement() throws {
        XCTAssertEqual(AccountRole.allCases.map(\.rawValue), ["guest", "user", "full_user", "admin"])
        for role in AccountRole.allCases {
            let json = "\"\(role.rawValue)\""
            XCTAssertEqual(try decode(AccountRole.self, json), role)
            XCTAssertEqual(String(data: try JSONEncoder().encode(role), encoding: .utf8), json)
        }
        XCTAssertEqual(AccountRole.fullUser.title, "Vollbenutzer")
    }

    func testPasswordPolicyMatchesCodePointMinimumAndUTF8Maximum() {
        XCTAssertFalse(AccountPasswordPolicy.accepts("123456789"))
        XCTAssertTrue(AccountPasswordPolicy.accepts("1234567890"))
        XCTAssertTrue(AccountPasswordPolicy.accepts(String(repeating: "a", count: 72)))
        XCTAssertFalse(AccountPasswordPolicy.accepts(String(repeating: "a", count: 73)))
        XCTAssertTrue(AccountPasswordPolicy.accepts(String(repeating: "ä", count: 36)))
        XCTAssertFalse(AccountPasswordPolicy.accepts(String(repeating: "ä", count: 37)))
        XCTAssertTrue(AccountPasswordPolicy.accepts(String(repeating: "e\u{301}", count: 5)))
    }

    func testProfileAndUserListDecodeNullableDatesAndSQLiteFlags() throws {
        let profile = try decode(AccountProfile.self, #"{"id":4,"username":"member","role":"user","created_at":1234.5,"last_login_at":null,"password_enabled":false}"#)
        XCTAssertFalse(profile.passwordEnabled)
        XCTAssertNil(profile.lastLoginAt)
        let users = try decode(AdminUsers.self, #"{"users":[{"id":1,"username":"admin","role":"admin","disabled":false,"created_at":1,"last_login_at":2},{"id":2,"username":"disabled","role":"user","disabled":1,"created_at":1,"last_login_at":null}]}"#)
        XCTAssertEqual(users.users.map { $0.disabled }, [false, true])
    }

    func testProfileAndSessionRequestsPreserveServerBasePathAndBearer() async throws {
        let client = try await client()
        MockURLProtocol.respond(json: #"{"id":4,"username":"member","role":"user","created_at":1234.5,"last_login_at":null,"password_enabled":true}"#)
        _ = try await client.accountProfile()
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/account/profile")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "GET")
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer account-token")
        MockURLProtocol.respond(json: #"{"sessions":[{"id":"opaque_session-123","created_at":1,"last_seen_at":2,"expires_at":999,"client_label":"iPhone","is_current":true}]}"#)
        let response = try await client.accountSessions()
        XCTAssertEqual(response.sessions.first?.id, "opaque_session-123")
        XCTAssertEqual(response.sessions.first?.isCurrent, true)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/account/sessions")
    }

    func testPasswordAndSelfDeletionOnlySendTheirConfirmationFields() async throws {
        let client = try await client()
        MockURLProtocol.respond(json: #"{"ok":true,"reauthenticate":true}"#)
        _ = try await client.changePassword(current: "old-password", new: "new-password")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/account/password")
        XCTAssertEqual(try body() as? [String: String], ["current_password": "old-password", "new_password": "new-password"])
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer account-token")
        _ = try await client.deleteAccount(currentPassword: "")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "DELETE")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/account/profile")
        let deletion = try body()
        XCTAssertEqual(Set(deletion.keys), Set(["current_password", "delete_household", "confirmation"]))
        XCTAssertEqual(deletion["current_password"] as? String, "")
        XCTAssertEqual(deletion["delete_household"] as? Bool, false)
        XCTAssertEqual(deletion["confirmation"] as? String, "")
    }

    func testHouseholdDeletionSendsOnlyTheExplicitPasswordAndConfirmation() async throws {
        let client = try await client()
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        _ = try await client.deleteAccount(currentPassword: "confirmed-password", deleteHousehold: true,
                                           confirmation: "HAUSHALT LÖSCHEN")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "DELETE")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/account/profile")
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer account-token")
        let deletion = try body()
        XCTAssertEqual(Set(deletion.keys), Set(["current_password", "delete_household", "confirmation"]))
        XCTAssertEqual(deletion["current_password"] as? String, "confirmed-password")
        XCTAssertEqual(deletion["delete_household"] as? Bool, true)
        XCTAssertEqual(deletion["confirmation"] as? String, "HAUSHALT LÖSCHEN")
    }

    func testSessionRevocationAndBothLogoutScopesUseProtectedEndpoints() async throws {
        let client = try await client()
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        _ = try await client.revokeSession(id: "session-123_abc")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "DELETE")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/account/sessions/session-123_abc")
        _ = try await client.logout()
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/auth/logout")
        _ = try await client.logoutAll()
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/auth/logout-all")
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer account-token")
        do {
            _ = try await client.revokeSession(id: "../profile")
            XCTFail("A session ID must not select a different route")
        } catch let error as APIError {
            guard case .invalidResponse = error else { return XCTFail("Unexpected error: \(error)") }
        }
    }

    func testAdminCRUDKeepsUsernameImmutableAndOmitsUnchangedFields() async throws {
        let client = try await client()
        MockURLProtocol.respond(json: #"{"users":[]}"#)
        _ = try await client.users()
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/users")
        MockURLProtocol.respond(json: #"{"ok":true,"id":7,"username":"new-user","role":"user"}"#)
        _ = try await client.createUser(username: "new-user", password: "safe-test-password", role: .user)
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(try body() as? [String: String], ["username": "new-user", "password": "safe-test-password", "role": "user"])
        _ = try await client.updateUser(id: 7, patch: AdminUserPatch(role: .admin))
        XCTAssertEqual(MockURLProtocol.lastMethod(), "PATCH")
        XCTAssertEqual(try body() as? [String: String], ["role": "admin"])
        _ = try await client.updateUser(id: 7, patch: AdminUserPatch(disabled: true))
        XCTAssertEqual(Set(try body().keys), ["disabled"])
        XCTAssertEqual(try body()["disabled"] as? Bool, true)
        _ = try await client.updateUser(id: 7, patch: AdminUserPatch(password: "another-safe-password"))
        XCTAssertEqual(Set(try body().keys), ["password"])
        _ = try await client.revokeUserSessions(id: 7)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/users/7/revoke-sessions")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        _ = try await client.deleteUser(id: 7)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/users/7")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "DELETE")
    }

    func testPublicDiscoveryOnlyAdvertisesKnownEnabledProviders() async throws {
        let client = try await client()
        MockURLProtocol.respond(json: #"{"providers":[{"id":"apple","name":"Apple","enabled":true},{"id":"google","name":"Google","enabled":false},{"id":"unknown","name":"Future","enabled":true}]}"#)
        let response = try await client.authProviders()
        XCTAssertEqual(response.available.map(\.id), ["apple"])
        XCTAssertNil(MockURLProtocol.lastHeader("Authorization"))
    }

    func testProviderLoginIsPublicButLinkRequiresCurrentBearer() async throws {
        let client = try await client()
        MockURLProtocol.respond(json: #"{"authorization_url":"https://accounts.example/auth","flow_id":"flow-1"}"#)
        let challenge = String(repeating: "A", count: 43)
        _ = try await client.startNativeAuth(provider: .google, intent: .login, challenge: challenge,
                                           invitationToken: "https://example.de/register?invite=invite-token", currentPassword: "must-not-be-sent")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/auth/google/start")
        XCTAssertNil(MockURLProtocol.lastHeader("Authorization"))
        XCTAssertEqual(try body() as? [String: String], ["platform": "native", "intent": "login", "code_challenge": challenge, "invitation_token": "invite-token"])
        _ = try await client.startNativeAuth(provider: .apple, intent: .link, challenge: challenge)
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer account-token")
        XCTAssertEqual(try body()["intent"] as? String, "link")
        XCTAssertNil(try body()["invitation_token"])
        XCTAssertNil(try body()["current_password"])
        _ = try await client.startNativeAuth(provider: .google, intent: .link, challenge: challenge, currentPassword: "local-confirmation")
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer account-token")
        XCTAssertEqual(try body()["current_password"] as? String, "local-confirmation")
        MockURLProtocol.respond(json: #"{"token":"new-token","username":"member","expires_in":3600}"#)
        _ = try await client.exchangeNativeAuth(code: "one-use-code", verifier: "proof-verifier")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/auth/exchange")
        XCTAssertNil(MockURLProtocol.lastHeader("Authorization"))
        XCTAssertEqual(try body() as? [String: String], ["code": "one-use-code", "code_verifier": "proof-verifier"])
    }

    func testIdentityUnlinkSendsConfirmationWithoutExposingProviderTokens() async throws {
        let client = try await client()
        MockURLProtocol.respond(json: #"{"identities":[{"provider":"apple","email":null,"linked_at":123}],"providers":[]}"#)
        let identities = try await client.accountIdentities()
        XCTAssertEqual(identities.identities.first?.title, "Apple")
        XCTAssertNil(identities.identities.first?.email)
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        _ = try await client.unlinkIdentity(provider: .apple, currentPassword: "confirmation")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/account/identities/apple")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "DELETE")
        XCTAssertEqual(try body() as? [String: String], ["current_password": "confirmation"])
    }

    func testProtectedAccountGuardFailureRetainsServerExplanation() async throws {
        let client = try await client()
        MockURLProtocol.respond(body: #"{"detail":"Der Haushalt hat noch Daten."}"#, statusCode: 409)
        do {
            _ = try await client.deleteAccount(currentPassword: "confirmed")
            XCTFail("Expected protected deletion to fail")
        } catch let error as APIError {
            guard case let .server(status, message) = error else { return XCTFail("Unexpected error: \(error)") }
            XCTAssertEqual(status, 409)
            XCTAssertEqual(message, "Der Haushalt hat noch Daten.")
        }
        let request = try await client.imageRequest(recipeID: 1)
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer account-token")
    }
}
