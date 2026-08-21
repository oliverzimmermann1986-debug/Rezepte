import Foundation

struct LoginResponse: Codable {
    let token: String
    let username: String
    let expiresIn: Int
}

struct SessionResponse: Codable {
    let username: String
    let fullAccess: Bool?
}

struct RecipeListResponse: Codable {
    let total: Int
    let items: [RecipeSummary]
}

struct RecipeFilters: Equatable, Sendable {
    var type = ""
    var category = ""
    var tagIDs: Set<Int> = []
    var includedIngredients: Set<String> = []
    var excludedIngredients: Set<String> = []
    var favoriteOnly = false
    var minRating = 0
    var manualOnly = false

    var activeCount: Int {
        (type.isEmpty ? 0 : 1)
            + (category.isEmpty ? 0 : 1)
            + tagIDs.count
            + includedIngredients.count
            + excludedIngredients.count
            + (favoriteOnly ? 1 : 0)
            + (minRating > 0 ? 1 : 0)
            + (manualOnly ? 1 : 0)
    }
}

struct RecipeFacets: Codable, Sendable {
    let types: [String]
    let categories: [String]
    let tags: [TagFacet]
    let ingredients: [IngredientFacet]

    static let empty = RecipeFacets(types: [], categories: [], tags: [], ingredients: [])
}

struct TagFacet: Codable, Identifiable, Hashable, Sendable {
    let id: Int
    let name: String
    let n: Int
}

struct IngredientFacet: Codable, Identifiable, Hashable, Sendable {
    let canonicalName: String
    let displayName: String
    let n: Int

    var id: String { canonicalName }
}

struct RecipeSummary: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let type: String?
    let category: String?
    let url: String?
    let isFavorite: Bool
    let rating: Int
    let servings: Int?
    let ingredientsCount: Int
    let stepsCount: Int
    let needsManualCare: Bool
    let description: String?
}

struct Recipe: Codable, Identifiable {
    let id: Int
    let name: String
    let type: String?
    let category: String?
    let url: String?
    var isFavorite: Bool
    let rating: Int?
    let servings: Int?
    let description: String?
    var ingredients: [Ingredient]
    var steps: [RecipeStep]
    let needsManualCare: Bool
    let manualCareReasons: [String]
}

struct Ingredient: Codable, Identifiable, Hashable {
    let id: Int?
    var name: String
    var amount: Double?
    var unit: String?
    var canonicalName: String?

    var stableID: String {
        if let id { return "ingredient-\(id)" }
        return "\(name)-\(amount ?? -1)-\(unit ?? "")"
    }

    var displayText: String {
        let quantity = amount.map {
            $0.rounded() == $0 ? String(Int($0)) : String(format: "%.2f", $0)
        }
        return [quantity, unit, name]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: " ")
    }
}

extension Ingredient {
    var idValue: String { stableID }
}

struct RecipeStep: Codable, Identifiable, Hashable {
    let id: Int?
    var stepNumber: Int?
    var instruction: String
    var timerSeconds: Int?

    var stableID: String {
        if let id { return "step-\(id)" }
        return "\(stepNumber ?? 0)-\(instruction)"
    }
}

struct CartResponse: Codable {
    let items: [CartItem]
}

struct CartItem: Codable, Identifiable, Hashable {
    let id: Int
    var name: String
    var amount: Double?
    let unit: String?
    var checked: Bool

    var displayText: String {
        let quantity = amount.map {
            $0.rounded() == $0 ? String(Int($0)) : String(format: "%.2f", $0)
        }
        return [quantity, unit, name]
            .compactMap { $0 }
            .filter { !$0.isEmpty }
            .joined(separator: " ")
    }
}

struct MealWeek: Codable {
    let weekStart: String
    let weekEnd: String
    let previousWeek: String
    let nextWeek: String
    let isCurrentWeek: Bool
    let days: [MealDay]
    let shoppingPreview: [ShoppingPreview]
    let summary: MealSummary
}

struct MealDay: Codable, Identifiable {
    let date: String
    let label: String
    let shortLabel: String
    let dayNumber: Int
    let isToday: Bool
    let items: [MealEntry]

    var id: String { date }
}

struct MealEntry: Codable, Identifiable {
    let id: Int
    let recipeId: Int
    let recipeName: String
    let plannedFor: String
    let plannedServings: Int
    let recipeServings: Int?
    let multiplier: Double
    let scalable: Bool
}

struct ShoppingPreview: Codable, Identifiable {
    let name: String
    let amount: Double?
    let unit: String?

    var id: String { "\(name)-\(amount ?? -1)-\(unit ?? "")" }
}

struct MealSummary: Codable {
    let plannedMeals: Int
    let plannedDays: Int
    let shoppingItems: Int
}

struct AdminOverview: Codable {
    let counts: AdminCounts
    let pdfCount: Int
    let dbSizeBytes: Int
}

struct AdminCounts: Codable {
    let recipes: Int
    let pending: Int
    let failedDownloads: Int
    let openFindings: Int
    let versions: Int
    let trash: Int
}

struct PendingItem: Codable, Identifiable {
    let url: String
    let name: String?
    let type: String?
    let status: String?
    let reason: String?

    var id: String { url }
}

struct APIResult: Codable {
    let ok: Bool?
    let message: String?
    let status: String?
    let added: Int?
}
