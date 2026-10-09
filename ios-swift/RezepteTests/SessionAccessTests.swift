import Foundation
import XCTest
@testable import Rezepte

final class SessionAccessTests: XCTestCase {
    @MainActor
    func testNormalAccountKeepsSharedLinksWithoutImportingEvenWhenLegacyFlagSaysAdmin() async throws {
        let fixture = try Fixture()
        defer { fixture.cleanUp() }
        fixture.links = ["https://example.org/recipe"]
        MockURLProtocol.respond(json: response(role: "user"))
        await fixture.store.restore()

        XCTAssertEqual(fixture.store.role, .user)
        XCTAssertFalse(fixture.store.fullAccess)
        XCTAssertFalse(fixture.store.canImport)
        XCTAssertTrue(fixture.store.supports("ai-shopping-optimization"), "Server capabilities are not account permissions")
        XCTAssertFalse(fixture.store.readOnly, "Manual recipe, planning and shopping actions remain available")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/system/info", "Restore must not dispatch a shared import")
        XCTAssertEqual(fixture.links, ["https://example.org/recipe"])
        XCTAssertTrue(fixture.store.alertMessage?.contains("Vollbenutzer und Admins") == true)

        await fixture.store.drainSharedImports()
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/system/info")
        fixture.store.signOut()
        XCTAssertEqual(fixture.links, ["https://example.org/recipe"], "A denied import must not disappear on sign-out")
    }

    @MainActor
    func testGuestKeepsSharedLinksAndReceivesAnExplanation() async throws {
        let fixture = try Fixture()
        defer { fixture.cleanUp() }
        fixture.links = ["https://example.org/recipe"]
        MockURLProtocol.respond(json: response(role: "guest"))
        await fixture.store.restore()

        XCTAssertTrue(fixture.store.readOnly)
        XCTAssertFalse(fixture.store.fullAccess)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/system/info")
        XCTAssertEqual(fixture.links.count, 1)
        XCTAssertTrue(fixture.store.alertMessage?.contains("nicht verarbeitet") == true)
        fixture.store.signOut()
        XCTAssertEqual(fixture.links.count, 1)
    }

    @MainActor
    func testAdministratorStillImportsSharedLinksAndRemovesOnlySuccessfulEntries() async throws {
        let fixture = try Fixture()
        defer { fixture.cleanUp() }
        fixture.links = ["https://example.org/recipe"]
        MockURLProtocol.respond(json: response(role: "admin"))
        await fixture.store.restore()

        XCTAssertTrue(fixture.store.fullAccess)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/pending/import-url")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertTrue(fixture.links.isEmpty)

        fixture.links = ["https://example.org/retry"]
        MockURLProtocol.respond(body: #"{"detail":"Import derzeit nicht verfügbar"}"#, statusCode: 503)
        await fixture.store.drainSharedImports()
        XCTAssertEqual(fixture.links, ["https://example.org/retry"])
        XCTAssertTrue(fixture.store.alertMessage?.contains("noch nicht importiert") == true)
    }

    @MainActor
    func testRoleDowngradeDuringImportStopsTheRemainingQueueAndPreservesLinks() async throws {
        let fixture = try Fixture()
        defer { fixture.cleanUp() }
        MockURLProtocol.respond(json: response(role: "admin"))
        await fixture.store.restore()
        fixture.links = ["https://example.org/first", "https://example.org/second"]
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        let started = expectation(description: "First import started")
        MockURLProtocol.suspendNextResponse { started.fulfill() }
        let importing = Task { await fixture.store.drainSharedImports() }
        await fulfillment(of: [started], timeout: 2)

        MockURLProtocol.respond(json: response(role: "user"))
        await fixture.store.refreshAccess()
        MockURLProtocol.resumeResponse()
        await importing.value

        XCTAssertFalse(fixture.store.fullAccess)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/auth/session", "No second import after privilege loss")
        XCTAssertEqual(fixture.links.count, 2)
    }

    @MainActor
    func testFullUserImportsWithoutSystemAdministrationRights() async throws {
        let fixture = try Fixture()
        defer { fixture.cleanUp() }
        fixture.links = ["https://example.org/recipe"]
        MockURLProtocol.respond(json: response(role: "full_user"))
        await fixture.store.restore()

        XCTAssertEqual(fixture.store.role, .fullUser)
        XCTAssertTrue(fixture.store.canImport)
        XCTAssertFalse(fixture.store.fullAccess)
        XCTAssertFalse(fixture.store.readOnly)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/pending/import-url")
        XCTAssertTrue(fixture.links.isEmpty)
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: String])
        XCTAssertEqual(body["visibility"], "private")
    }

    @MainActor
    func testNamedGuestCannotSendMutationsButCanLogOut() async throws {
        let fixture = try Fixture()
        defer { fixture.cleanUp() }
        MockURLProtocol.respond(json: response(role: "guest"))
        await fixture.store.restore()
        XCTAssertTrue(fixture.store.readOnly)
        XCTAssertFalse(fixture.store.canImport)

        do {
            _ = try await fixture.store.api.duplicateRecipe(id: 42, newName: "Denied")
            XCTFail("Guest mutation must be blocked before the request")
        } catch APIError.server(let status, _) {
            XCTAssertEqual(status, 403)
        }
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/system/info")
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        _ = try await fixture.store.api.logout()
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/auth/logout")
    }

    @MainActor
    func testNamedGuestCanManageOnlyOwnSecurity() async throws {
        let fixture = try Fixture()
        defer { fixture.cleanUp() }
        MockURLProtocol.respond(json: response(role: "guest"))
        await fixture.store.restore()
        XCTAssertTrue(fixture.store.canManageOwnAccount)
        XCTAssertTrue(fixture.store.readOnly)
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        _ = try await fixture.store.api.changePassword(current: "old-test-password", new: "new-test-password")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/account/password")
        _ = try await fixture.store.api.revokeSession(id: "synthetic-session")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/account/sessions/synthetic-session")
        _ = try await fixture.store.api.logoutAll()
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/auth/logout-all")
        _ = try await fixture.store.api.unlinkIdentity(provider: .google, currentPassword: "test-password")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/account/identities/google")
        _ = try await fixture.store.api.deleteAccount(currentPassword: "test-password")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/account/profile")

        MockURLProtocol.respond(json: #"{"authorization_url":"https://appleid.apple.com/auth/authorize","flow_id":"synthetic-flow"}"#)
        _ = try await fixture.store.api.startNativeAuth(provider: .apple, intent: .link, challenge: "synthetic-challenge", currentPassword: "test-password")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/auth/apple/start")
        do {
            _ = try await fixture.store.api.createInvitation()
            XCTFail("Guest account security must not grant household writes")
        } catch APIError.server(let status, _) { XCTAssertEqual(status, 403) }
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/auth/apple/start")
    }

    @MainActor
    func testAnonymousGuestCannotUseAccountSecurity() async throws {
        let fixture = try Fixture()
        defer { fixture.cleanUp() }
        MockURLProtocol.respond(json: response(role: "guest").replacingOccurrences(of: "\"id\":12,", with: ""))
        await fixture.store.restore()
        XCTAssertFalse(fixture.store.canManageOwnAccount)
        do {
            _ = try await fixture.store.api.changePassword(current: "old-test-password", new: "new-test-password")
            XCTFail("Anonymous guests have no account security actions")
        } catch APIError.server(let status, _) { XCTAssertEqual(status, 403) }
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/system/info")
    }

    private func response(role: String) -> String {
        // Extra fields let one response serve the session and server-info requests.
        #"{"id":12,"username":"test-account","role":"\#(role)","full_access":true,"read_only":false,"name":"Rezepte","version":"1.9.0","capabilities":["shopping-categories","recurring-shopping","weekly-meal-plan","ai-shopping-optimization"],"ok":true}"#
    }

    @MainActor
    func testNormalAccountCreatesAManualVariantUsingOnlyItsChosenName() async throws {
        let fixture = try Fixture()
        defer { fixture.cleanUp() }
        MockURLProtocol.respond(json: response(role: "user"))
        await fixture.store.restore()
        MockURLProtocol.respond(json: #"{"ok":true,"recipe_id":55}"#)

        let result = try await fixture.store.api.duplicateRecipe(id: 42, newName: "Meine Variante")

        XCTAssertEqual(result.recipeId, 55)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/recipes/42/duplicate")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: String])
        XCTAssertEqual(body, ["new_name": "Meine Variante"])
        XCTAssertFalse(fixture.store.fullAccess)
    }
}

@MainActor
private final class Fixture {
    let suite = "SessionAccessTests.\(UUID().uuidString)"
    let defaults: UserDefaults
    var links: [String] = []
    private var token: String? = "test-token"
    lazy var store: SessionStore = {
        let persistence = LocalSessionPersistence(
            defaults: defaults, read: { [weak self] in self?.token }, save: { [weak self] in self?.token = $0 },
            delete: { [weak self] in self?.token = nil; return true }
        )
        return SessionStore(
            api: APIClient(session: MockURLProtocol.makeSession()), defaults: defaults,
            persistence: persistence,
            sharedImportURLs: { [weak self] in self?.links ?? [] },
            removeSharedImport: { [weak self] url in self?.links.removeAll { $0 == url } }
        )
    }()

    init() throws {
        defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defaults.set("https://example.de", forKey: "server-url")
    }

    func cleanUp() {
        defaults.removePersistentDomain(forName: suite)
    }
}
