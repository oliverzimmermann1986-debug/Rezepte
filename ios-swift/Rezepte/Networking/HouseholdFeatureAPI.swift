import Foundation

extension APIClient {
    func householdCookbooks(recipeID: Int? = nil) async throws -> [HouseholdCookbook] {
        let query = recipeID.map { [URLQueryItem(name: "recipe_id", value: String($0))] } ?? []
        let response: HouseholdItems<HouseholdCookbook> = try await kitchenRequest("/api/cookbooks", query: query)
        return response.items
    }

    func saveCookbook(name: String, id: Int? = nil) async throws -> HouseholdCookbook {
        let response: HouseholdItem<HouseholdCookbook> = try await kitchenRequest(
            id.map { "/api/cookbooks/\($0)" } ?? "/api/cookbooks",
            method: id == nil ? "POST" : "PATCH", body: CookbookNameBody(name: name))
        return response.item
    }

    func cookbookRecipes(id: Int) async throws -> [HouseholdRecipe] {
        let response: HouseholdItems<HouseholdRecipe> = try await kitchenRequest("/api/cookbooks/\(id)/recipes")
        return response.items
    }

    func cookbookMembership(id: Int, recipeID: Int, included: Bool) async throws {
        let _: APIResult = try await kitchenRequest("/api/cookbooks/\(id)/recipes/\(recipeID)", method: included ? "PUT" : "DELETE")
    }

    func deleteCookbook(id: Int) async throws {
        let _: APIResult = try await kitchenRequest("/api/cookbooks/\(id)", method: "DELETE")
    }

    func cookingNotes(recipeID: Int) async throws -> [CookingNote] {
        let response: HouseholdItems<CookingNote> = try await kitchenRequest("/api/cook-notes", query: [URLQueryItem(name: "recipe_id", value: String(recipeID))])
        return response.items
    }

    func saveCookingNote(historyID: Int, note: String) async throws -> CookingNote {
        let response: HouseholdItem<CookingNote> = try await kitchenRequest("/api/cook-notes/\(historyID)", method: "PUT", body: CookingNoteBody(note: note))
        return response.item
    }

    func saveCookingPhoto(historyID: Int, jpeg: Data) async throws -> CookingNote {
        let response: HouseholdItem<CookingNote> = try await kitchenUpload("/api/cook-notes/\(historyID)/photo", jpeg: jpeg)
        return response.item
    }

    func removeCookingPhoto(historyID: Int) async throws -> CookingNote {
        let response: HouseholdItem<CookingNote> = try await kitchenRequest("/api/cook-notes/\(historyID)/photo", method: "DELETE")
        return response.item
    }

    func mealWishes(weekStart: String) async throws -> [MealWish] {
        let response: HouseholdItems<MealWish> = try await kitchenRequest("/api/meal-wishes", query: [URLQueryItem(name: "week_start", value: weekStart)])
        return response.items
    }

    func addMealWish(weekStart: String, recipeID: Int) async throws {
        let _: APIResult = try await kitchenRequest("/api/meal-wishes", method: "POST", body: MealWishBody(weekStart: weekStart, recipeId: recipeID))
    }

    func voteMealWish(id: Int, voted: Bool) async throws {
        let _: APIResult = try await kitchenRequest("/api/meal-wishes/\(id)/vote", method: "PUT", body: WishVoteBody(voted: voted))
    }

    func deleteMealWish(id: Int) async throws {
        let _: APIResult = try await kitchenRequest("/api/meal-wishes/\(id)", method: "DELETE")
    }

    func planMealWish(id: Int, request: WishPlanRequest) async throws {
        let _: APIResult = try await kitchenRequest("/api/meal-wishes/\(id)/plan", method: "POST", body: request)
    }

    func mealSuggestions(count: Int, vegetarian: Int, maxMinutes: Int?, excluding: [Int]) async throws -> MealSuggestionResponse {
        try await kitchenRequest("/api/discovery/meal-plan", method: "POST", body: MealSuggestionsBody(
            count: count, vegetarianCount: vegetarian, maxMinutes: maxMinutes,
            excludeRecipeIds: Array(Set(excluding)).sorted()))
    }
}

private struct CookbookNameBody: Encodable { let name: String }
private struct CookingNoteBody: Encodable { let note: String }
private struct MealWishBody: Encodable { let weekStart: String; let recipeId: Int }
private struct WishVoteBody: Encodable { let voted: Bool }
private struct MealSuggestionsBody: Encodable {
    let count: Int
    let vegetarianCount: Int
    let maxMinutes: Int?
    let excludeRecipeIds: [Int]
}
