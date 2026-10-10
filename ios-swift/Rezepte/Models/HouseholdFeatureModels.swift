import Foundation

struct HouseholdItems<Item: Decodable>: Decodable { let items: [Item] }
struct HouseholdItem<Item: Decodable>: Decodable { let item: Item }

struct HouseholdCookbook: Decodable, Identifiable {
    let id: Int
    let name: String
    let recipeCount: Int
    let containsRecipe: Bool
}

struct HouseholdRecipe: Decodable, Identifiable {
    let id: Int
    let name: String
}

struct CookingNote: Decodable, Identifiable {
    let historyId: Int
    let cookedAt: Double
    let cookedBy: String
    let servings: Int?
    let note: String
    let photoUrl: String?
    let canEdit: Bool
    let updatedAt: Double?
    var id: Int { historyId }
}

struct MealWish: Decodable, Identifiable {
    let id: Int
    let recipeId: Int
    let recipeName: String
    let createdBy: String
    let votes: Int
    let myVote: Bool
    let canDelete: Bool
    let plannedFor: String?
    let plannedServings: Int?
}

struct MealSuggestion: Decodable, Identifiable {
    let recipeId: Int
    let name: String
    let servings: Int?
    let totalMinutes: Double?
    let vegetarian: Bool
    var id: Int { recipeId }
}

struct MealSuggestionResponse: Decodable {
    let items: [MealSuggestion]
    let warnings: [String]
}

/// Once an upsert starts, its tuple stays immutable until the server confirms it.
struct MealSuggestionDraft: Identifiable {
    enum Status { case pending, uncertain, saved }
    var suggestion: MealSuggestion
    var date: String
    var servings: Int
    var status: Status = .pending
    var id: Int { suggestion.recipeId }

    static func make(_ suggestions: [MealSuggestion], days: [MealDay]) -> [Self] {
        let ordered = days.filter { $0.items.isEmpty } + days.filter { !$0.items.isEmpty }
        var seen = Set<Int>()
        return zip(suggestions.filter { seen.insert($0.recipeId).inserted }, ordered).map { item, day in
            Self(suggestion: item, date: day.date, servings: min(24, max(1, item.servings ?? 2)))
        }
    }
}

struct WishPlanRequest: Encodable, Equatable {
    let plannedFor: String
    let plannedServings: Int
}
