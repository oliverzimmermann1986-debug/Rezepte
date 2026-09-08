import Foundation
import XCTest
@testable import Rezepte

final class OfflineCookingTests: XCTestCase {
    private var directory: URL!
    private var store: OfflineStore!
    private var account: OfflineAccount!

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        store = OfflineStore(directory: directory)
        account = try XCTUnwrap(OfflineAccount(server: "https://example.de", username: "oliver"))
    }

    override func tearDownWithError() throws {
        if FileManager.default.fileExists(atPath: directory.path) {
            try FileManager.default.removeItem(at: directory)
        }
    }

    func testAccountIdentityNormalizesServerButSeparatesAccountsAndBasePaths() throws {
        XCTAssertEqual(account, OfflineAccount(server: " HTTPS://EXAMPLE.DE:443/ ", username: "oliver"))
        XCTAssertNotEqual(account.id, OfflineAccount(server: "https://example.de", username: "other")?.id)
        XCTAssertNotEqual(account.id, OfflineAccount(server: "https://other.de", username: "oliver")?.id)
        XCTAssertNotEqual(account.id, OfflineAccount(server: "https://example.de/other", username: "oliver")?.id)
        XCTAssertNil(OfflineAccount(server: "http://example.de", username: "oliver"))
        XCTAssertNil(OfflineAccount(server: "https://example.de", username: ""))
    }

    func testRecipePersistsAcrossStoreInstancesAndNeverLeaksAcrossAccounts() throws {
        let recipe = try fixture()
        try store.saveRecipe(recipe, account: account)
        let restored = OfflineStore(directory: directory)
        XCTAssertEqual(try restored.recipes(account: account).first?.recipe.name, "Pasta")
        let other = try XCTUnwrap(OfflineAccount(server: "https://other.de", username: "oliver"))
        XCTAssertTrue(try restored.recipes(account: other).isEmpty)
        try store.write(["private note"], key: "memory-drafts", account: account)
        try store.clear(account: account)
        XCTAssertTrue(try restored.recipes(account: account).isEmpty)
        XCTAssertNil(try restored.read([String].self, key: "memory-drafts", account: account))
        XCTAssertThrowsError(try store.saveRecipe(recipe, account: account))
        store.activate(account: account)
        XCTAssertNoThrow(try store.saveRecipe(recipe, account: account))
    }

    func testTimerUsesDeadlineAcrossRelaunchAndPause() throws {
        let start = Date(timeIntervalSince1970: 10_000)
        var timer = PersistentCookingTimer(deadline: nil, pausedSeconds: 300)
        timer.start(duration: 300, now: start)
        try store.write(timer, key: "timer-example", account: account)
        var reloaded = try XCTUnwrap(OfflineStore(directory: directory).read(PersistentCookingTimer.self,
            key: "timer-example", account: account))
        XCTAssertEqual(reloaded.remaining(at: start.addingTimeInterval(90)), 210)
        reloaded.pause(now: start.addingTimeInterval(90))
        XCTAssertEqual(reloaded.remaining(at: start.addingTimeInterval(900)), 210)
        reloaded.start(duration: 300, now: start.addingTimeInterval(900))
        XCTAssertEqual(reloaded.remaining(at: start.addingTimeInterval(1_120)), 0)
    }

    func testLocalStepsPersistAndOldNetworkAcknowledgementCannotEraseNewerTap() throws {
        var first = LocalCookingProgress(recipe: try fixture(), servings: 2)
        first.started = true
        first.completedSteps = [0]
        first.changed()
        try store.saveProgress(first, account: account)
        var latest = first
        latest.activeStep = 1
        latest.completedSteps = [0, 1]
        latest.changed()
        try store.saveProgress(latest, account: account)
        try store.markProgressSynced(first, account: account)
        let restored = try XCTUnwrap(OfflineStore(directory: directory).progress(account: account).first)
        XCTAssertEqual(restored.completedSteps, [0, 1])
        XCTAssertTrue(restored.needsSync)
        try store.markProgressSynced(latest, account: account)
        XCTAssertFalse(try XCTUnwrap(store.progress(account: account).first).needsSync)
    }

    func testFinishingIsDurableAndRetriesUseOneCompletionKey() throws {
        var progress = LocalCookingProgress(recipe: try fixture(), servings: 2)
        progress.started = true
        progress.completedSteps = [0, 1]
        progress.changed()
        try store.saveProgress(progress, account: account)
        try store.finish(progress, account: account)
        try store.finish(progress, account: account)
        let restored = OfflineStore(directory: directory)
        XCTAssertEqual(try restored.completions(account: account).map(\.id), [progress.runID])
        XCTAssertTrue(try restored.progress(account: account).isEmpty)
        try restored.removeCompletion(id: progress.runID, account: account)
        XCTAssertTrue(try restored.completions(account: account).isEmpty)
    }

    func testCrashAfterOutboxWriteDoesNotResumeAlreadyCompletedRun() throws {
        let progress = LocalCookingProgress(recipe: try fixture(), servings: 2)
        try store.saveProgress(progress, account: account)
        try store.write([PendingCookingCompletion(id: progress.runID, recipeID: progress.recipeID, servings: 2)],
            key: "cooking-completions", account: account)
        XCTAssertTrue(try OfflineStore(directory: directory).progress(account: account).isEmpty)
        try store.removeCompletion(id: progress.runID, account: account)
        XCTAssertTrue(try OfflineStore(directory: directory).progress(account: account).isEmpty)
    }

    func testOfflineFallbackRejectsAuthenticationSecurityAndMalformedResponses() {
        XCTAssertTrue(APIError.permitsOfflineFallback(URLError(.notConnectedToInternet)))
        XCTAssertTrue(APIError.permitsOfflineFallback(URLError(.timedOut)))
        XCTAssertTrue(APIError.permitsOfflineFallback(APIError.server(503, "Unavailable")))
        XCTAssertFalse(APIError.permitsOfflineFallback(APIError.unauthenticated))
        XCTAssertFalse(APIError.permitsOfflineFallback(APIError.server(403, "Forbidden")))
        XCTAssertFalse(APIError.permitsOfflineFallback(APIError.server(404, "Gone")))
        XCTAssertFalse(APIError.permitsOfflineFallback(APIError.cloudflareAccessRequired))
        XCTAssertFalse(APIError.permitsOfflineFallback(URLError(.serverCertificateUntrusted)))
        XCTAssertFalse(APIError.permitsOfflineFallback(APIError.invalidResponse("recipe")))
        XCTAssertFalse(APIError.permitsOfflineFallback(URLError(.cancelled)))
    }

    func testRecipeNetworkFallbackUsesVerifiedAccountButNever401() async throws {
        try store.saveRecipe(try fixture(), account: account)
        let client = APIClient(session: MockURLProtocol.makeSession(), offlineStore: store)
        try await client.configure(server: account.server, token: "token")
        await client.setOfflineAccount(account)
        MockURLProtocol.respond(body: "{\"detail\":\"Unavailable\"}", statusCode: 503)
        let recipe = try await client.recipe(id: 42)
        XCTAssertEqual(recipe.name, "Pasta")
        let offline = await client.recipeWasLoadedOffline(id: 42)
        XCTAssertTrue(offline)
        MockURLProtocol.respond(body: "{\"detail\":\"Expired\"}", statusCode: 401)
        do {
            _ = try await client.recipe(id: 42)
            XCTFail("An unauthorized request must never return private cached data")
        } catch APIError.unauthenticated { }
        try await client.configure(server: account.server, token: "different-user-token")
        MockURLProtocol.respond(body: "{\"detail\":\"Unavailable\"}", statusCode: 503)
        do {
            _ = try await client.recipe(id: 42)
            XCTFail("Reconfiguration must invalidate the previous cache identity")
        } catch APIError.server(503, _) { }
    }

    func testPendingCompletionCannotUseAnotherAccountsCredentials() async throws {
        let client = APIClient(session: MockURLProtocol.makeSession(), offlineStore: store)
        try await client.configure(server: account.server, token: "token-a")
        await client.setOfflineAccount(account)
        let other = try XCTUnwrap(OfflineAccount(server: account.server, username: "other"))
        try await client.configure(server: other.server, token: "token-b")
        await client.setOfflineAccount(other)
        MockURLProtocol.respond(json: "{\"ok\":true}")
        do {
            _ = try await client.completeCooking(id: 42, servings: 2, idempotencyKey: "old-run", expectedAccount: account)
            XCTFail("A pending write must not borrow another account's credentials")
        } catch is CancellationError { }
        do {
            _ = try await client.updateCookingProgress(id: 42, completedSteps: [0], activeStep: 1,
                                                       servings: 2, expectedAccount: account)
            XCTFail("A progress write must be bound to its original account")
        } catch is CancellationError { }
    }

    @MainActor
    func testOfflineRestoreRetainsTokenAndGuestReadOnlyRole() async throws {
        let defaultsName = "OfflineCookingTests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: defaultsName))
        let tokenKey = "offline-test-\(UUID().uuidString)"
        defer { defaults.removePersistentDomain(forName: defaultsName); KeychainStore.delete(account: tokenKey) }
        defaults.set(account.server, forKey: "server-url")
        defaults.set(try JSONEncoder().encode(account), forKey: "offline-account-v1")
        try KeychainStore.save("test-token", account: tokenKey)
        try store.write(OfflineSession(account: account, tokenFingerprint: OfflineAccount.digest("test-token"),
            fullAccess: false, readOnly: true, capabilities: [], serverVersion: "1.7.0"), key: "session", account: account)
        let api = APIClient(session: MockURLProtocol.makeSession(), offlineStore: store)
        let session = SessionStore(api: api, defaults: defaults, offlineStore: store, tokenAccount: tokenKey)
        MockURLProtocol.respond(body: "{\"detail\":\"Unavailable\"}", statusCode: 503)
        await session.restore()
        XCTAssertEqual(session.state, .signedIn)
        XCTAssertTrue(session.isOffline)
        XCTAssertTrue(session.readOnly)
        XCTAssertFalse(session.fullAccess)
        XCTAssertEqual(KeychainStore.read(account: tokenKey), "test-token")
        MockURLProtocol.respond(body: "{\"detail\":\"Expired\"}", statusCode: 401)
        await session.refreshAccess()
        XCTAssertEqual(session.state, .signedOut)
        XCTAssertNil(KeychainStore.read(account: tokenKey))
        XCTAssertNil(try store.read(OfflineSession.self, key: "session", account: account))
    }

    func testCachedSessionIsBoundToServerAndTokenFingerprint() {
        let cached = OfflineSession(account: account, tokenFingerprint: OfflineAccount.digest("token-a"),
            fullAccess: false, readOnly: false, capabilities: [], serverVersion: "1")
        XCTAssertTrue(cached.matches(server: "https://EXAMPLE.DE/", token: "token-a"))
        XCTAssertFalse(cached.matches(server: "https://other.de", token: "token-a"))
        XCTAssertFalse(cached.matches(server: "https://example.de", token: "token-b"))
    }

    private func fixture() throws -> Recipe {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(Recipe.self, from: Data("""
        {"id":42,"name":"Pasta","is_favorite":false,"servings":2,"ingredients":[],
         "steps":[{"id":1,"instruction":"Wasser kochen"},{"id":2,"instruction":"Nudeln kochen"}],
         "needs_manual_care":false,"manual_care_reasons":[]}
        """.utf8))
    }
}
