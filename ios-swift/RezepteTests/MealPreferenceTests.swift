import Foundation
import XCTest
@testable import Rezepte

final class MealPreferenceTests: XCTestCase {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: Data(json.utf8))
    }

    func testOlderSuggestionsWithoutPreferenceReasonsStillDecode() throws {
        let result = try decode(MealSuggestionResponse.self, #"{"items":[{"recipe_id":1,"name":"Suppe","vegetarian":true},{"recipe_id":2,"name":"Pasta","vegetarian":false,"preference_reasons":null}],"warnings":[]}"#)
        XCTAssertEqual(result.items.map(\.id), [1, 2])
        XCTAssertTrue(result.items.allSatisfy { $0.preferenceReasons == nil })
        let local = MealSuggestion(recipeId: 3, name: "Kartoffeln", servings: 2, totalMinutes: 20, vegetarian: true)
        XCTAssertNil(local.preferenceReasons)
    }

    func testPreferenceReasonsSurviveLegacyDraftAssignment() throws {
        let result = try decode(MealSuggestionResponse.self, #"{"items":[{"recipe_id":1,"name":"Suppe","vegetarian":true,"preference_reasons":["Favorit im Haushalt","5 von 5 Sternen im Haushalt"]},{"recipe_id":2,"name":"Pasta","vegetarian":false,"preference_reasons":[]}],"warnings":[]}"#)
        let day = MealDay(date: "2026-10-19", label: "Montag", shortLabel: "Mo", dayNumber: 19, isToday: false, items: [])
        let rows = MealSuggestionDraft.make(result.items, days: [day])
        XCTAssertEqual(rows.first?.suggestion.preferenceReasons, ["Favorit im Haushalt", "5 von 5 Sternen im Haushalt"])
        XCTAssertEqual(result.items.last?.preferenceReasons, [])
    }

    func testConstrainedResponsePreservesReasonsAndFixedDayInDraft() throws {
        let result = try decode(ConstrainedMealSuggestionResponse.self, #"{"items":[{"recipe_id":42,"name":"Pasta","servings":3,"vegetarian":true,"categories":["pasta"],"weekday":5,"preference_reasons":["4 von 5 Sternen im Haushalt"]}],"warnings":[],"missing_quotas":[],"constraints_satisfied":true,"search_complete":true}"#)
        let day = MealDay(date: "2026-10-23", label: "Freitag", shortLabel: "Fr", dayNumber: 23, isToday: false, items: [])
        let week = MealWeek(weekStart: "2026-10-19", weekEnd: "2026-10-25", previousWeek: "", nextWeek: "", isCurrentWeek: false,
                            days: [day], shoppingPreview: [], summary: MealSummary(plannedMeals: 0, plannedDays: 0, shoppingItems: 0))
        let rows = try MealWeekdayDates.drafts(result.items, week: week, requireWeekdays: true)
        XCTAssertEqual(rows.first?.date, "2026-10-23")
        XCTAssertEqual(rows.first?.servings, 3)
        XCTAssertEqual(rows.first?.suggestion.preferenceReasons, ["4 von 5 Sternen im Haushalt"])
        XCTAssertEqual(result.items.first?.categories, [.pasta])
    }
}
