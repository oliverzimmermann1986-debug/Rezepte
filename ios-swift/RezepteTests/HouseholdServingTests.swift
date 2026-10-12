import Foundation
import XCTest
@testable import Rezepte

final class HouseholdServingTests: XCTestCase {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: Data(json.utf8))
    }

    func testOlderServersDefaultToTwoAndNewSharedPreferenceDecodes() throws {
        let old = try decode(SessionResponse.self, #"{"id":12,"username":"member","role":"user"}"#)
        XCTAssertEqual(HouseholdServingDefaults.resolve(old.defaultServings), 2)
        let current = try decode(SessionResponse.self, #"{"id":12,"username":"member","role":"user","household_id":8,"default_servings":6}"#)
        XCTAssertEqual(current.householdId, 8)
        XCTAssertEqual(HouseholdServingDefaults.resolve(current.defaultServings), 6)
        let house = try decode(HouseholdAccount.self, #"{"id":8,"is_guest":false,"is_owner":false,"members":[],"invitations":[],"max_members":2,"default_servings":6}"#)
        XCTAssertEqual(house.defaultServings, 6)
        for invalid in [0, -1, 25] { XCTAssertEqual(HouseholdServingDefaults.resolve(invalid), 2) }
    }

    func testFreshCookingUsesDefaultAndRunningProgressRetainsChosenPortions() {
        let fresh = CookingProgress(recipeId: 1, username: "member", completedSteps: [], activeStep: 0,
            servings: 2, startedAt: nil, updatedAt: nil, exists: false, stepCount: 2)
        let running = CookingProgress(recipeId: 1, username: "member", completedSteps: [0], activeStep: 1,
            servings: 7, startedAt: 1, updatedAt: 2, exists: true, stepCount: 2)
        XCTAssertEqual(HouseholdServingDefaults.cooking(initial: 4, original: 2, progress: fresh), 4)
        XCTAssertEqual(HouseholdServingDefaults.cooking(initial: 4, original: 2, progress: running), 7)
    }

    func testPreferenceRequestSendsOnlyChosenPortionsAndPreservesBasePath() async throws {
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de/rezepte", token: "member-token")
        MockURLProtocol.respond(json: #"{"ok":true,"household_id":8,"default_servings":4}"#)
        let response = try await client.saveHouseholdDefaultServings(4)
        XCTAssertEqual(response.householdId, 8)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/account/preferences")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "PATCH")
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer member-token")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Int])
        XCTAssertEqual(body, ["default_servings": 4])
    }

    @MainActor
    func testSessionRefreshAndPersistedSnapshotUseHouseholdDefaultAndSignoutClearsIt() async throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "servings-session-\(UUID())"))
        defaults.set("https://example.de", forKey: "server-url")
        let persistence = LocalSessionPersistence(defaults: defaults, read: { "member-token" }, save: { _ in }, delete: { true })
        let store = SessionStore(api: APIClient(session: MockURLProtocol.makeSession()), defaults: defaults,
            persistence: persistence, sharedImportURLs: { [] }, removeSharedImport: { _ in })
        MockURLProtocol.respond(json: #"{"id":12,"username":"member","role":"user","household_id":8,"default_servings":4,"name":"Rezepte","version":"1.15.3","capabilities":["household-default-servings-v1"]}"#)
        await store.restore()
        XCTAssertEqual(store.defaultServings, 4)
        MockURLProtocol.respond(json: #"{"ok":true,"household_id":8,"default_servings":6}"#)
        try await store.saveHouseholdDefaultServings(6)
        XCTAssertEqual(store.defaultServings, 6)
        let snapshot = try JSONDecoder().decode(OfflineSessionSnapshot.self, from: XCTUnwrap(defaults.data(forKey: "offline-session-v1")))
        XCTAssertEqual(snapshot.session.defaultServings, 6)
        MockURLProtocol.respond(json: #"{"id":12,"username":"member","role":"user","household_id":8,"default_servings":5}"#)
        await store.refreshAccess()
        XCTAssertEqual(store.defaultServings, 5)
        store.signOut()
        XCTAssertEqual(store.defaultServings, 2); XCTAssertNil(store.householdID)
        XCTAssertNil(defaults.data(forKey: "offline-session-v1"))
    }

    @MainActor
    func testLatePreferenceSaveCannotUpdateAnotherSession() async throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "servings-race-\(UUID())"))
        defaults.set("https://example.de", forKey: "server-url")
        let persistence = LocalSessionPersistence(defaults: defaults, read: { "member-token" }, save: { _ in }, delete: { true })
        let store = SessionStore(api: APIClient(session: MockURLProtocol.makeSession()), defaults: defaults,
            persistence: persistence, sharedImportURLs: { [] }, removeSharedImport: { _ in })
        MockURLProtocol.respond(json: #"{"id":12,"username":"member","role":"user","household_id":8,"default_servings":4,"name":"Rezepte","version":"1.15.3","capabilities":["household-default-servings-v1"]}"#)
        await store.restore()
        let started = expectation(description: "save started")
        MockURLProtocol.respond(json: #"{"ok":true,"household_id":8,"default_servings":6}"#)
        MockURLProtocol.suspendNextResponse { started.fulfill() }
        let save = Task { try await store.saveHouseholdDefaultServings(6) }
        await fulfillment(of: [started], timeout: 2)
        store.signOut(); MockURLProtocol.resumeResponse()
        do { try await save.value; XCTFail("Late response must fail") } catch { }
        XCTAssertEqual(store.defaultServings, 2); XCTAssertNil(store.householdID)
    }
}
