import Foundation
import XCTest
@testable import Rezepte

final class MealPlanEditingTests: XCTestCase {
    func testSelectedLocalDaySurvivesMidnightAndDaylightSavingTime() throws {
        let berlin = try XCTUnwrap(TimeZone(identifier: "Europe/Berlin"))
        let formatter = ISO8601DateFormatter()
        for (instant, expected) in [("2026-10-24T22:30:00Z", "2026-10-25"),
                                    ("2026-03-28T23:30:00Z", "2026-03-29"),
                                    ("2026-12-31T23:30:00Z", "2027-01-01")] {
            XCTAssertEqual(MealWeekdayDates.localDate(try XCTUnwrap(formatter.date(from: instant)), timeZone: berlin), expected)
        }
        let instant = try XCTUnwrap(formatter.date(from: "2026-10-12T01:30:00Z"))
        XCTAssertEqual(MealWeekdayDates.localDate(instant, timeZone: TimeZone(secondsFromGMT: -25_200)!), "2026-10-11")
    }

    func testLegacyProfileGetsTwoPortionsAndNewProfilesRoundTrip() throws {
        let old = try JSONDecoder().decode(MealPlanningPreferences.self, from: Data(#"{"count":7,"vegetarian":3,"maxMinutes":30}"#.utf8))
        XCTAssertEqual(old.servings, 2)
        XCTAssertEqual(old.count, 7)
        var value = old; value.servings = 6
        XCTAssertEqual(try JSONDecoder().decode(MealPlanningPreferences.self, from: JSONEncoder().encode(value)), value)
        value.servings = 0; XCTAssertNotNil(value.validationMessage)
        value.servings = 25; XCTAssertNotNil(value.validationMessage)
    }

    func testAddMealSendsSelectedDayAndPublishesRefreshAfterSuccess() async throws {
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de", token: "test-token")
        let plan = expectation(description: "selected day refresh")
        let cart = expectation(description: "shopping refresh")
        let planObserver = NotificationCenter.default.addObserver(forName: .mealPlanChanged, object: nil, queue: nil) { event in
            XCTAssertEqual(event.object as? String, "2026-10-25")
            plan.fulfill()
        }
        let cartObserver = NotificationCenter.default.addObserver(forName: .shoppingChanged, object: nil, queue: nil) { _ in cart.fulfill() }
        defer { NotificationCenter.default.removeObserver(planObserver); NotificationCenter.default.removeObserver(cartObserver) }
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        _ = try await client.addMeal(date: "2026-10-25", recipeID: 42, servings: 6)
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Any])
        XCTAssertEqual(body["planned_for"] as? String, "2026-10-25")
        XCTAssertEqual(body["planned_servings"] as? Int, 6)
        XCTAssertEqual(body["recipe_id"] as? Int, 42)
        await fulfillment(of: [plan, cart], timeout: 1)
    }

    func testFailedSaveDoesNotAnnouncePlanOrShoppingChange() async throws {
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de", token: "test-token")
        let changed = expectation(description: "no refresh for failed write"); changed.isInverted = true
        let observers = [Notification.Name.mealPlanChanged, .shoppingChanged].map { name in
            NotificationCenter.default.addObserver(forName: name, object: nil, queue: nil) { _ in changed.fulfill() }
        }
        defer { observers.forEach { NotificationCenter.default.removeObserver($0) } }
        MockURLProtocol.respond(body: #"{"detail":"Rezept nicht gefunden"}"#, statusCode: 404)
        do { _ = try await client.addMeal(date: "2026-10-25", recipeID: 42, servings: 6); XCTFail("Must fail") }
        catch { }
        await fulfillment(of: [changed], timeout: 0.05)
    }

    func testReplacementCarriesExpectedRecipeAndPortionsAndFreshSeedIsEncoded() async throws {
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de", token: "test-token")
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        _ = try await client.replaceMeal(id: 9, recipeID: 42, expectedRecipeID: 17, servings: 4)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/meal-plan/items/9/replace")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Int])
        XCTAssertEqual(body, ["recipe_id": 42, "expected_recipe_id": 17, "planned_servings": 4])
        let seed = UUID()
        MockURLProtocol.respond(json: #"{"items":[],"warnings":[],"missing_quotas":[],"constraints_satisfied":true,"search_complete":true}"#)
        _ = try await client.constrainedMealSuggestions(count: 1, vegetarian: 0, maxMinutes: nil, excluding: [], quotas: [],
            excludedIngredients: [], avoidRecentDays: 14, weekStart: "2026-10-12", randomSeed: seed)
        let suggestionsBody = try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Any])
        XCTAssertEqual(suggestionsBody["random_seed"] as? String, seed.uuidString)
    }
}
