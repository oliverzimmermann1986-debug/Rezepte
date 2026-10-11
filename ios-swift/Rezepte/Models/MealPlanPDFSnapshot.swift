import Foundation

/// Immutable snapshot: the server loads recipe names and checks the selected rows itself.
struct MealPlanPDFSnapshot: Encodable, Identifiable {
    let id = UUID()
    let criteria: Criteria
    let items: [Item]

    struct Criteria: Encodable {
        let count: Int
        let vegetarianCount: Int
        let maxMinutes: Int?
        let excludeRecipeIds: [Int]
        let categoryQuotas: [MealCategoryQuota]
        let excludedIngredients: [String]
        let avoidRecentDays: Int
        let weekStart: String
        let weekdayRules: [MealWeekdayRule]
        let householdOnly: Bool?
        let quotaMode: String
        let additionalCourses: [MealExtraCourseSlot]?
    }

    struct Item: Encodable {
        let recipeId: Int
        let date: String
        let servings: Int
        let mealCourse: MealExtraCourse?
    }

    private enum CodingKeys: String, CodingKey { case criteria, items }

    init(preferences: MealPlanningPreferences, weekStart: String, rows: [MealSuggestionDraft], excluding: [Int] = []) {
        let constraints = preferences.constraints
        let selectedIDs = Set(rows.map(\.id))
        criteria = Criteria(count: preferences.count, vegetarianCount: preferences.vegetarian,
                            maxMinutes: preferences.maxMinutes == 0 ? nil : preferences.maxMinutes,
                            excludeRecipeIds: Array(Set(excluding).subtracting(selectedIDs)).sorted(), categoryQuotas: constraints.quotas,
                            excludedIngredients: constraints.excludedIngredients,
                            avoidRecentDays: constraints.avoidRecentDays, weekStart: weekStart,
                            weekdayRules: constraints.weekdayRules, householdOnly: constraints.householdOnly ? true : nil,
                            quotaMode: "minimum", additionalCourses: constraints.extraCourseSlots.isEmpty ? nil : constraints.extraCourseSlots)
        items = rows.map { Item(recipeId: $0.id, date: $0.date, servings: $0.servings, mealCourse: $0.mealCourse) }
    }
}
