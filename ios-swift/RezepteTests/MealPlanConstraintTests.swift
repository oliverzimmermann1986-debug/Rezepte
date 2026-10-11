import Foundation
import XCTest
@testable import Rezepte

final class MealPlanConstraintTests: XCTestCase {
    func testExactQuotasAllowOverlapAndPreserveZeroInsteadOfTreatingItAsUnset() {
        var constraints = MealPlanConstraints()
        constraints.counts = [.pasta: 2, .potato: 2, .rice: 0]
        let categories: [Int: Set<MealIngredientCategory>] = [1: [.pasta, .potato], 2: [.pasta, .potato]]
        XCTAssertEqual(constraints.quotas.count, 3)
        XCTAssertEqual(constraints.fulfilled(.pasta, recipeIDs: [1, 1, 2], categories: categories), 2)
        XCTAssertEqual(constraints.fulfilled(.potato, recipeIDs: [1, 2], categories: categories), 2)
        XCTAssertEqual(constraints.replacementQuotas(remainingIDs: [1], categories: categories), [
            MealCategoryQuota(category: .pasta, count: 1), MealCategoryQuota(category: .potato, count: 1),
            MealCategoryQuota(category: .rice, count: 0),
        ])
        XCTAssertNil(constraints.replacementQuotas(remainingIDs: [], categories: categories))
        constraints.counts.removeValue(forKey: .rice)
        XCTAssertFalse(constraints.quotas.contains { $0.category == .rice })
    }

    func testReplacementKeepsAlreadySatisfiedQuotaAtZero() {
        var constraints = MealPlanConstraints()
        constraints.counts = [.pasta: 1, .potato: 1]
        let result = constraints.replacementQuotas(remainingIDs: [1], categories: [1: [.pasta]])
        XCTAssertEqual(result, [MealCategoryQuota(category: .pasta, count: 0), MealCategoryQuota(category: .potato, count: 1)])
    }

    func testIngredientExclusionsParseWithoutDroppingEntriesOrInventingSubstrings() {
        var constraints = MealPlanConstraints()
        constraints.excludedIngredientsText = " Pilze, SELLERIE; Pilze\nSellerie, Reisessig "
        XCTAssertEqual(constraints.excludedIngredients, ["Pilze", "SELLERIE", "Reisessig"])
        XCTAssertNil(constraints.validationMessage)
        constraints.excludedIngredientsText = String(repeating: "x", count: 201)
        XCTAssertNotNil(constraints.validationMessage)
        constraints.excludedIngredientsText = (1...101).map { "Zutat\($0)" }.joined(separator: ",")
        XCTAssertEqual(constraints.excludedIngredients.count, 101)
        XCTAssertNotNil(constraints.validationMessage)
    }

    func testConstrainedSuggestionsSendSelectedWeekAndZeroQuotaAndDecodeDeficits() async throws {
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de/rezepte", token: "plan-token")
        MockURLProtocol.respond(json: #"{"items":[{"recipe_id":42,"name":"Pasta mit Kartoffeln","servings":2,"total_minutes":25,"vegetarian":true,"categories":["pasta","potato"]}],"warnings":["Nur ein passendes Gericht"],"missing_quotas":[{"category":"pasta","requested":2,"fulfilled":1,"missing":1}],"constraints_satisfied":false,"search_complete":true}"#)
        let result = try await client.constrainedMealSuggestions(count: 7, vegetarian: 2, maxMinutes: 30, excluding: [3, 1, 3],
            quotas: [MealCategoryQuota(category: .pasta, count: 2), MealCategoryQuota(category: .rice, count: 0)],
            excludedIngredients: ["Sellerie"], avoidRecentDays: 7, weekStart: "2026-10-12")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/discovery/meal-plan")
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer plan-token")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Any])
        XCTAssertEqual(body["week_start"] as? String, "2026-10-12")
        XCTAssertEqual(body["avoid_recent_days"] as? Int, 7)
        XCTAssertEqual(body["exclude_recipe_ids"] as? [Int], [1, 3])
        XCTAssertEqual(body["excluded_ingredients"] as? [String], ["Sellerie"])
        let quotas = try XCTUnwrap(body["category_quotas"] as? [[String: Any]])
        XCTAssertEqual(quotas[1]["category"] as? String, "rice")
        XCTAssertEqual(quotas[1]["count"] as? Int, 0)
        XCTAssertEqual(result.items[0].categories, [.pasta, .potato])
        XCTAssertEqual(result.items[0].recipe.id, 42)
        XCTAssertEqual(result.missingQuotas[0].missing, 1)
        XCTAssertFalse(result.constraintsSatisfied)
        XCTAssertTrue(result.searchComplete)
    }
}
