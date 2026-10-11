import Foundation

enum MealIngredientCategory: String, Codable, CaseIterable, Identifiable {
    case pasta, potato, rice, legume
    var id: String { rawValue }
    var title: String {
        switch self {
        case .pasta: return "Pasta"
        case .potato: return "Kartoffeln"
        case .rice: return "Reis"
        case .legume: return "Hülsenfrüchte"
        }
    }
}

struct MealCategoryQuota: Codable, Equatable {
    let category: MealIngredientCategory
    let count: Int
}

struct MealPlanConstraints: Equatable {
    var counts: [MealIngredientCategory: Int] = [:]
    var excludedIngredientsText = ""
    var avoidRecentDays = 0

    var quotas: [MealCategoryQuota] {
        MealIngredientCategory.allCases.compactMap { category in
            counts[category].map { MealCategoryQuota(category: category, count: $0) }
        }
    }

    var excludedIngredients: [String] {
        var seen = Set<String>()
        return excludedIngredientsText.components(separatedBy: CharacterSet(charactersIn: ",;\n"))
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty && seen.insert($0.lowercased()).inserted }
    }

    var validationMessage: String? {
        if excludedIngredients.count > 100 { return "Bitte höchstens 100 Zutaten ausschließen." }
        if excludedIngredients.contains(where: { $0.count > 200 }) { return "Eine ausgeschlossene Zutat darf höchstens 200 Zeichen enthalten." }
        return nil
    }

    func fulfilled(_ category: MealIngredientCategory, recipeIDs: [Int], categories: [Int: Set<MealIngredientCategory>]) -> Int {
        Set(recipeIDs).filter { categories[$0]?.contains(category) == true }.count
    }

    /// Count each dish in every matching family; overlapping quotas need no sum limit.
    func replacementQuotas(remainingIDs: [Int], categories: [Int: Set<MealIngredientCategory>]) -> [MealCategoryQuota]? {
        var result: [MealCategoryQuota] = []
        for quota in quotas {
            let remaining = quota.count - fulfilled(quota.category, recipeIDs: remainingIDs, categories: categories)
            guard (0...1).contains(remaining) else { return nil }
            result.append(MealCategoryQuota(category: quota.category, count: remaining))
        }
        return result
    }
}

struct ConstrainedMealSuggestion: Decodable {
    let recipe: MealSuggestion
    let categories: Set<MealIngredientCategory>
    private enum CodingKeys: String, CodingKey { case categories }

    init(from decoder: Decoder) throws {
        recipe = try MealSuggestion(from: decoder)
        let container = try decoder.container(keyedBy: CodingKeys.self)
        categories = Set(try container.decode([String].self, forKey: .categories).compactMap(MealIngredientCategory.init(rawValue:)))
    }
}

struct MealQuotaShortfall: Decodable {
    let category: MealIngredientCategory
    let requested: Int
    let fulfilled: Int
    let missing: Int
}

struct ConstrainedMealSuggestionResponse: Decodable {
    let items: [ConstrainedMealSuggestion]
    let warnings: [String]
    let missingQuotas: [MealQuotaShortfall]
    let constraintsSatisfied: Bool
    let searchComplete: Bool
}
