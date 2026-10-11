import Foundation
import XCTest
@testable import Rezepte

@MainActor
final class KitchenShortcutDateBoundaryTests: XCTestCase {
    func testTodayRequestAnchorsServerWeekToDeviceMonday() async throws {
        let defaults = UserDefaults(suiteName: "shortcut-date-\(UUID().uuidString)")!
        defaults.set("https://example.de", forKey: "server-url")
        let persistence = LocalSessionPersistence(defaults: defaults, read: { "date-test-token" }, save: { _ in }, delete: { true })
        let session = SessionStore(api: APIClient(session: MockURLProtocol.makeSession()), defaults: defaults,
            persistence: persistence, sharedImportURLs: { [] }, removeSharedImport: { _ in })
        MockURLProtocol.respond(json: #"{"id":12,"username":"test","role":"user","name":"Rezepte","version":"1.12.0","capabilities":[],"ok":true}"#)
        await session.restore()
        MockURLProtocol.respond(json: #"{"week_start":"2026-10-12","week_end":"2026-10-18","previous_week":"2026-10-05","next_week":"2026-10-19","is_current_week":false,"days":[{"date":"2026-10-12","label":"Montag","short_label":"Mo","day_number":12,"is_today":false,"items":[{"id":1,"recipe_id":42,"recipe_name":"Pasta","planned_for":"2026-10-12","planned_servings":2,"recipe_servings":2,"multiplier":1,"scalable":true}]}],"shopping_preview":[],"summary":{"planned_meals":1,"planned_days":1,"shopping_items":0}}"#)
        let coordinator = KitchenShortcutCoordinator()
        coordinator.bind(session: session)
        let utcSunday = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-10-11T23:00:00Z"))
        let count = try await coordinator.openToday(now: utcSunday, timeZone: TimeZone(secondsFromGMT: 7_200)!)
        XCTAssertEqual(MockURLProtocol.lastQueryItems()["week_start"], "2026-10-12")
        XCTAssertEqual(count, 1)
        guard case .recipe(42)? = coordinator.route?.destination else { return XCTFail("Device Monday must open its planned recipe") }
    }

    func testDeviceSundayCanStillRequestPreviousWeekWhenServerIsMonday() throws {
        let utcMonday = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-10-12T01:00:00Z"))
        XCTAssertEqual(KitchenShortcutCoordinator.localDate(utcMonday, timeZone: TimeZone(secondsFromGMT: -25_200)!), "2026-10-11")
    }
}
