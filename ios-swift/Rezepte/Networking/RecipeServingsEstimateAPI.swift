import Foundation

extension APIClient {
    func estimateRecipeServings(id: Int, consent: AIProcessingConsent) async throws -> RecipeServingsEstimate {
        let result: RecipeServingsEstimate = try await kitchenAIRequest("/api/recipes/\(id)/servings-estimate",
            body: RecipeServingsEstimateBody(), consent: consent, action: .servingsEstimate, timeout: 120)
        return try result.validated()
    }

    func applyRecipeServingsEstimate(id: Int, servings: Int, fingerprint: String) async throws -> RecipeServingsApplyResponse {
        guard (1...50).contains(servings), RecipeServingsEstimate.isValidFingerprint(fingerprint) else {
            throw APIError.invalidResponse("Portionsangabe")
        }
        let response: RecipeServingsApplyResponse = try await kitchenRequest("/api/recipes/\(id)/servings-estimate/apply",
            method: "POST", body: RecipeServingsApplyBody(servings: servings, fingerprint: fingerprint))
        guard response.ok, response.servings == servings else { throw APIError.invalidResponse("Portionsangabe wurde nicht bestätigt.") }
        return response
    }
}

private struct RecipeServingsEstimateBody: Encodable {}
private struct RecipeServingsApplyBody: Encodable {
    let servings: Int
    let fingerprint: String
}
