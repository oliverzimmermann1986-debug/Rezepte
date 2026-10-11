import Foundation
import XCTest
@testable import Rezepte

final class MealPlanPDFTests: XCTestCase {
    private func snapshot() -> MealPlanPDFSnapshot {
        let preferences = MealPlanningPreferences(count: 2, vegetarian: 1, maxMinutes: 30,
            constraints: MealPlanConstraints(counts: [.pasta: 1], excludedIngredientsText: "Pilze, Sellerie",
                avoidRecentDays: 14, weekdayRules: [MealWeekdayRule(weekday: 5, category: .pasta, maxMinutes: 20)]))
        let dish = MealSuggestion(recipeId: 42, name: "Private recipe name", servings: 2, totalMinutes: 20, vegetarian: true)
        return MealPlanPDFSnapshot(preferences: preferences, weekStart: "2026-10-12",
                                   rows: [MealSuggestionDraft(suggestion: dish, date: "2026-10-16", servings: 6, status: .saved)],
                                   excluding: [7, 42, 7])
    }

    func testSnapshotRetainsEditedSelectionAndRulesWithoutClientClaimsOrNames() throws {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: encoder.encode(snapshot())) as? [String: Any])
        XCTAssertEqual(Set(object.keys), Set(["criteria", "items"]))
        let criteria = try XCTUnwrap(object["criteria"] as? [String: Any])
        XCTAssertEqual(criteria["exclude_recipe_ids"] as? [Int], [7])
        XCTAssertEqual(criteria["max_minutes"] as? Int, 30)
        XCTAssertEqual(criteria["excluded_ingredients"] as? [String], ["Pilze", "Sellerie"])
        let rule = try XCTUnwrap((criteria["weekday_rules"] as? [[String: Any]])?.first)
        XCTAssertEqual(rule["weekday"] as? Int, 5)
        XCTAssertEqual(rule["category"] as? String, "pasta")
        let row = try XCTUnwrap((object["items"] as? [[String: Any]])?.first)
        XCTAssertEqual(Set(row.keys), Set(["recipe_id", "date", "servings"]))
        XCTAssertEqual(row["date"] as? String, "2026-10-16")
        XCTAssertEqual(row["servings"] as? Int, 6)
    }

    func testPDFTransportUsesAuthenticatedJSONSnapshotAndRejectsNonPDF() async throws {
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de", token: "plan-token")
        MockURLProtocol.respond(body: "%PDF-1.4\nsynthetic", headers: ["Content-Type": "application/pdf"])
        let data = try await client.mealPlanSuggestionsPDF(snapshot())
        XCTAssertTrue(data.starts(with: Data("%PDF-".utf8)))
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/discovery/meal-plan/pdf")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer plan-token")
        XCTAssertEqual(MockURLProtocol.lastHeader("Accept"), "application/pdf")
        XCTAssertEqual(MockURLProtocol.lastHeader("Content-Type"), "application/json")
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        do { _ = try await client.mealPlanSuggestionsPDF(snapshot()); XCTFail("JSON must not be exported") }
        catch APIError.invalidResponse { }
    }

    func testGuestCannotPostSnapshot() async throws {
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de", token: "guest.test")
        do { _ = try await client.mealPlanSuggestionsPDF(snapshot()); XCTFail("Guest export must be rejected") }
        catch APIError.server(let code, _) { XCTAssertEqual(code, 403) }
    }

    func testExportRequiresPDFAndSafeFilename() throws {
        XCTAssertThrowsError(try PDFExportDocument(data: Data("<html>Login</html>".utf8), title: "Week"))
        let document = try PDFExportDocument(data: Data("%PDF-1.4".utf8), title: "../Woche/Privat\n")
        XCTAssertFalse(document.filename.contains("/"))
        XCTAssertFalse(document.filename.contains("\n"))
        XCTAssertTrue(document.filename.hasSuffix(".pdf"))
    }
}
