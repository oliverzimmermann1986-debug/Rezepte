import Foundation

extension APIClient {
    func constrainedMealSuggestions(count: Int, vegetarian: Int, maxMinutes: Int?, excluding: [Int],
                                   quotas: [MealCategoryQuota], excludedIngredients: [String],
                                   avoidRecentDays: Int, weekStart: String,
                                   weekdayRules: [MealWeekdayRule] = [], householdOnly: Bool = false,
                                   mealCourse: String? = nil, randomSeed: UUID? = nil) async throws -> ConstrainedMealSuggestionResponse {
        try await kitchenRequest("/api/discovery/meal-plan", method: "POST", body: ConstrainedMealPlanBody(
            count: count, vegetarianCount: vegetarian, maxMinutes: maxMinutes,
            excludeRecipeIds: Array(Set(excluding)).sorted(), categoryQuotas: quotas,
            excludedIngredients: excludedIngredients, avoidRecentDays: avoidRecentDays, weekStart: weekStart,
            weekdayRules: weekdayRules.isEmpty ? nil : weekdayRules, householdOnly: householdOnly ? true : nil,
            quotaMode: "minimum", mealCourse: mealCourse, randomSeed: randomSeed))
    }
}

private struct ConstrainedMealPlanBody: Encodable {
    let count: Int
    let vegetarianCount: Int
    let maxMinutes: Int?
    let excludeRecipeIds: [Int]
    let categoryQuotas: [MealCategoryQuota]
    let excludedIngredients: [String]
    let avoidRecentDays: Int
    let weekStart: String
    let weekdayRules: [MealWeekdayRule]?
    let householdOnly: Bool?
    let quotaMode: String
    let mealCourse: String?
    let randomSeed: UUID?
}
