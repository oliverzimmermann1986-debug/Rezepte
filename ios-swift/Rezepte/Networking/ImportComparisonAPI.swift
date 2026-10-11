import Foundation

struct ImportComparisonDraft: Codable {
    let name: String
    let description: String
    let servings: Int?
    let ingredients: [PendingIngredient]
    let steps: [PendingStep]
}

struct ImportComparisonRequest: Encodable {
    let url: String
    let visibility: String
    let draft: ImportComparisonDraft
}

struct ImportComparisonResponse: Decodable {
    let items: [ImportComparisonCandidate]
    let truncated: Bool
}

struct ImportComparisonCandidate: Decodable, Identifiable {
    let id: Int
    let name: String
    let url: String?
    let servings: Int?
    let ingredients: [PendingIngredient]
    let steps: [PendingStep]
    let score: Int
    let sharedIngredients: [String]
    let importOnlyIngredients: [String]
    let existingOnlyIngredients: [String]
    let snapshot: String
}

struct ImportComparisonResolution: Encodable {
    let url: String
    let visibility: String
    let draft: ImportComparisonDraft
    let requestId: UUID
    let recipeId: Int
    let snapshot: String
    let action: String
    let name: String
    let ingredientsFrom: String
    let stepsFrom: String
}

extension APIClient {
    func compareImport(_ request: ImportComparisonRequest) async throws -> ImportComparisonResponse {
        try await kitchenRequest("/api/import-comparison/preview", method: "POST", body: request)
    }

    func resolveImportComparison(_ request: ImportComparisonResolution) async throws -> APIResult {
        try await kitchenRequest("/api/import-comparison/resolve", method: "POST", body: request)
    }
}
