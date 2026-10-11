import Foundation

extension APIClient {
    func constrainedMealSuggestions(count: Int, vegetarian: Int, maxMinutes: Int?, excluding: [Int],
                                   quotas: [MealCategoryQuota], excludedIngredients: [String],
                                   avoidRecentDays: Int, weekStart: String) async throws -> ConstrainedMealSuggestionResponse {
        try await kitchenRequest("/api/discovery/meal-plan", method: "POST", body: ConstrainedMealPlanBody(
            count: count, vegetarianCount: vegetarian, maxMinutes: maxMinutes,
            excludeRecipeIds: Array(Set(excluding)).sorted(), categoryQuotas: quotas,
            excludedIngredients: excludedIngredients, avoidRecentDays: avoidRecentDays, weekStart: weekStart))
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
}
