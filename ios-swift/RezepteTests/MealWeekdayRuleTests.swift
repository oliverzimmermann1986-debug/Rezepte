import Foundation
import XCTest
@testable import Rezepte

final class MealWeekdayRuleTests: XCTestCase {
    private func decode<T: Decodable>(_ type: T.Type, _ text: String) throws -> T {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: Data(text.utf8))
    }

    private func week(start: String = "2026-10-19") -> MealWeek {
        let days = (1...7).map { day in
            MealDay(date: MealWeekdayDates.date(weekday: day, weekStart: start)!, label: MealWeekdayRule.title(day),
                    shortLabel: "Tag", dayNumber: day, isToday: false, items: [])
        }
        return MealWeek(weekStart: start, weekEnd: days.last!.date, previousWeek: "", nextWeek: "", isCurrentWeek: false,
                        days: days, shoppingPreview: [], summary: MealSummary(plannedMeals: 0, plannedDays: 0, shoppingItems: 0))
    }

    func testWeekdayDatesDoNotShiftAcrossDSTAndRequireMonday() {
        XCTAssertEqual(MealWeekdayDates.date(weekday: 7, weekStart: "2026-10-19"), "2026-10-25")
        XCTAssertEqual(MealWeekdayDates.date(weekday: 7, weekStart: "2026-03-23"), "2026-03-29")
        XCTAssertEqual(MealWeekdayDates.weekday(date: "2026-10-23", weekStart: "2026-10-19"), 5)
        XCTAssertNil(MealWeekdayDates.date(weekday: 5, weekStart: "2026-10-20"))
        XCTAssertNil(MealWeekdayDates.date(weekday: 8, weekStart: "2026-10-19"))
        XCTAssertNil(MealWeekdayDates.date(weekday: 1, weekStart: "2026-02-30"))
        XCTAssertNil(MealWeekdayDates.weekday(date: "2026-10-26", weekStart: "2026-10-19"))
    }

    func testRulesValidateDuplicateDaysCountAndConflictingTimes() {
        var value = MealPlanningPreferences()
        value.constraints.weekdayRules = [MealWeekdayRule(weekday: 1), MealWeekdayRule(weekday: 1)]
        XCTAssertNotNil(value.validationMessage)
        value.count = 1; value.vegetarian = 0
        value.constraints.weekdayRules = [MealWeekdayRule(weekday: 1), MealWeekdayRule(weekday: 5)]
        XCTAssertNotNil(value.validationMessage)
        value.constraints.weekdayRules = [MealWeekdayRule(weekday: 5, minMinutes: 45, maxMinutes: 20)]
        XCTAssertNotNil(value.validationMessage)
        value.constraints.weekdayRules = [MealWeekdayRule(weekday: 5, minMinutes: 45)]
        value.maxMinutes = 20
        XCTAssertNotNil(value.validationMessage)
        value.maxMinutes = 60
        XCTAssertNil(value.validationMessage)
        XCTAssertNotNil(MealWeekdayRule(weekday: 0).validationMessage)
        XCTAssertNotNil(MealWeekdayRule(weekday: 1, maxMinutes: 1441).validationMessage)
    }

    func testAssignedWeekdaysDriveDatesInsteadOfArrayPosition() throws {
        let items = try decode([ConstrainedMealSuggestion].self, #"[{"recipe_id":1,"name":"Pasta","servings":3,"vegetarian":true,"categories":["pasta"],"weekday":5},{"recipe_id":2,"name":"Suppe","servings":2,"vegetarian":true,"categories":[],"weekday":1}]"#)
        let rows = try MealWeekdayDates.drafts(items, week: week(), requireWeekdays: true)
        XCTAssertEqual(rows.map(\.date), ["2026-10-23", "2026-10-19"])
        XCTAssertEqual(rows.map(\.servings), [3, 2])
    }

    func testMalformedOrDuplicateAssignmentsAreRejected() throws {
        for weekdays in [["5", "5"], ["null", "1"], ["8", "1"]] {
            let items = try decode([ConstrainedMealSuggestion].self, "[{\"recipe_id\":1,\"name\":\"A\",\"vegetarian\":true,\"categories\":[],\"weekday\":\(weekdays[0])},{\"recipe_id\":2,\"name\":\"B\",\"vegetarian\":true,\"categories\":[],\"weekday\":\(weekdays[1])}]")
            XCTAssertThrowsError(try MealWeekdayDates.drafts(items, week: week(), requireWeekdays: true))
        }
    }

    func testMissingMovedAndUnsuitableDishesLeaveRuleUnmet() {
        let rule = MealWeekdayRule(weekday: 5, category: .pasta, maxMinutes: 20, vegetarian: true)
        let pasta = MealSuggestion(recipeId: 1, name: "Pasta", servings: 2, totalMinutes: 20, vegetarian: true)
        var row = MealSuggestionDraft(suggestion: pasta, date: "2026-10-23", servings: 2)
        XCTAssertTrue(MealWeekdayDates.unmetRules([rule], rows: [row], categories: [1: [.pasta]], weekStart: "2026-10-19").isEmpty)
        XCTAssertEqual(MealWeekdayDates.unmetRules([rule], rows: [], categories: [1: [.pasta]], weekStart: "2026-10-19"), [rule])
        row.date = "2026-10-22"
        XCTAssertEqual(MealWeekdayDates.unmetRules([rule], rows: [row], categories: [1: [.pasta]], weekStart: "2026-10-19"), [rule])
        row.date = "2026-10-23"
        XCTAssertEqual(MealWeekdayDates.unmetRules([rule], rows: [row], categories: [1: [.rice]], weekStart: "2026-10-19"), [rule])
        let unknownTime = MealSuggestion(recipeId: 2, name: "Unbekannte Zeit", servings: 2, totalMinutes: nil, vegetarian: true)
        XCTAssertFalse(rule.matches(unknownTime, categories: [.pasta]))
        let secondRow = MealSuggestionDraft(suggestion: unknownTime, date: row.date, servings: 2)
        XCTAssertEqual(MealWeekdayDates.unmetRules([rule], rows: [row, secondRow], categories: [1: [.pasta], 2: [.pasta]], weekStart: "2026-10-19"), [rule])
    }

    func testWeekdayRequestAndMissingDaysRoundTrip() async throws {
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de", token: "test-token")
        MockURLProtocol.respond(json: #"{"items":[{"recipe_id":42,"name":"Pasta","vegetarian":true,"categories":["pasta"],"weekday":5}],"warnings":[],"missing_quotas":[],"missing_weekdays":[7],"constraints_satisfied":false,"search_complete":true}"#)
        let result = try await client.constrainedMealSuggestions(count: 2, vegetarian: 1, maxMinutes: nil, excluding: [], quotas: [],
            excludedIngredients: [], avoidRecentDays: 0, weekStart: "2026-10-19",
            weekdayRules: [MealWeekdayRule(weekday: 5, category: .pasta, maxMinutes: 20, vegetarian: true), MealWeekdayRule(weekday: 7, minMinutes: 60)])
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Any])
        let rules = try XCTUnwrap(body["weekday_rules"] as? [[String: Any]])
        XCTAssertEqual(rules[0]["weekday"] as? Int, 5)
        XCTAssertEqual(rules[0]["category"] as? String, "pasta")
        XCTAssertEqual(rules[0]["max_minutes"] as? Int, 20)
        XCTAssertEqual(rules[0]["vegetarian"] as? Bool, true)
        XCTAssertEqual(rules[1]["min_minutes"] as? Int, 60)
        XCTAssertEqual(result.items.first?.weekday, 5)
        XCTAssertEqual(result.missingWeekdays, [7])
    }
}
