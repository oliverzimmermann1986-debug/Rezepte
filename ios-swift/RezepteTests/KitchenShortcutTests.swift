import Foundation
import XCTest
@testable import Rezepte

@MainActor
final class KitchenShortcutTests: XCTestCase {
    func testOneSpokenArticleUsesExistingQuantityParser() throws {
        XCTAssertEqual(try KitchenShortcutCoordinator.shoppingEntry("1 Liter Milch"), ShoppingAddItem(name: "Milch", amount: 1, unit: "l"))
        XCTAssertThrowsError(try KitchenShortcutCoordinator.shoppingEntry("Milch, Brot"))
        XCTAssertThrowsError(try KitchenShortcutCoordinator.shoppingEntry(" "))
        XCTAssertThrowsError(try KitchenShortcutCoordinator.shoppingEntry(String(repeating: "a", count: 201)))
    }

    func testTodayUsesLocalCalendarDateRatherThanStaleServerFlag() {
        let entry = MealEntry(id: 1, recipeId: 42, recipeName: "Pasta", plannedFor: "2026-10-12", plannedServings: 2, recipeServings: 2, multiplier: 1, scalable: true)
        let week = MealWeek(weekStart: "2026-10-12", weekEnd: "2026-10-18", previousWeek: "2026-10-05", nextWeek: "2026-10-19", isCurrentWeek: true,
            days: [MealDay(date: "2026-10-12", label: "Mo", shortLabel: "Mo", dayNumber: 12, isToday: false, items: [entry])], shoppingPreview: [],
            summary: MealSummary(plannedMeals: 1, plannedDays: 1, shoppingItems: 0))
        let now = ISO8601DateFormatter().date(from: "2026-10-11T23:00:00Z")!
        XCTAssertEqual(KitchenShortcutCoordinator.todayEntries(in: week, now: now, timeZone: TimeZone(secondsFromGMT: 7200)!).first?.recipeId, 42)
        XCTAssertTrue(KitchenShortcutCoordinator.todayEntries(in: week, now: now, timeZone: TimeZone(secondsFromGMT: 0)!).isEmpty)
    }

    func testSignedOutShortcutDoesNotQueueAnActionForNextLogin() async {
        let defaults = UserDefaults(suiteName: UUID().uuidString)!
        let persistence = LocalSessionPersistence(defaults: defaults, read: { nil }, save: { _ in }, delete: { true })
        let session = SessionStore(defaults: defaults, persistence: persistence)
        session.signOut()
        let coordinator = KitchenShortcutCoordinator()
        coordinator.bind(session: session)
        do { _ = try await coordinator.addShopping("Milch"); XCTFail("Signed-out action must fail") }
        catch { XCTAssertTrue(error is KitchenShortcutError) }
        XCTAssertNil(coordinator.route)
    }

    func testDisconnectedIntentPersistsExactlyOneOperationInSharedJournal() async throws {
        let defaults = UserDefaults(suiteName: UUID().uuidString)!
        defaults.set("https://example.de", forKey: "server-url")
        let persistence = LocalSessionPersistence(defaults: defaults, read: { "synthetic-token" }, save: { _ in }, delete: { true })
        let session = SessionStore(api: APIClient(session: MockURLProtocol.makeSession()), defaults: defaults, persistence: persistence,
                                   sharedImportURLs: { [] }, removeSharedImport: { _ in })
        MockURLProtocol.respond(json: #"{"id":12,"username":"test","role":"user","name":"Rezepte","version":"1.12.0","capabilities":[],"ok":true}"#)
        await session.restore()
        var files: [String: Data] = [:]
        let shopping = ShoppingSyncStore(storage: ShoppingJournalStorage(read: { files[$0] }, write: { files[$0] = $1 }, eraseNamespace: { _ in }), defaults: defaults)
        let identity = session.identity
        await shopping.activate(context: ShoppingSyncContext(identity: identity, namespace: "intent-test", writable: true,
            isCurrent: { session.identity == identity }, fetch: { ShoppingCartResponse(householdId: 7, items: []) },
            synchronize: { _ in throw URLError(.notConnectedToInternet) }))
        let coordinator = KitchenShortcutCoordinator()
        coordinator.bind(shopping: shopping, session: session)
        let synchronized = try await coordinator.addShopping("1 Liter Milch")
        XCTAssertFalse(synchronized)
        XCTAssertEqual(shopping.pendingCount, 1)
        XCTAssertEqual(shopping.items.first?.name, "Milch")
        XCTAssertTrue(files.values.contains(where: { String(data: $0, encoding: .utf8)?.contains("Milch") == true }))
        session.signOut()
        do { _ = try await coordinator.addShopping("Brot"); XCTFail("Must not add after sign-out") } catch {}
        XCTAssertEqual(shopping.pendingCount, 1)
    }
}
