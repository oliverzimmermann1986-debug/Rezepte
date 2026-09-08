import Foundation

extension APIClient {
    func cookingMemory(recipeID: Int, expectedAccount: OfflineAccount, offset: Int = 0) async throws -> CookingMemoryResponse {
        try requireOfflineAccount(expectedAccount)
        return try await send("/api/recipes/\(recipeID)/cooking-memory", query: [
            URLQueryItem(name: "limit", value: "100"), URLQueryItem(name: "offset", value: String(offset))
        ])
    }

    func saveCookingMemory(recipeID: Int, request: CookingMemoryRequest, expectedAccount: OfflineAccount) async throws -> CookingMemorySavedResponse {
        try requireOfflineAccount(expectedAccount)
        return try await send("/api/recipes/\(recipeID)/cooking-memory", method: "POST", body: request)
    }

    func deleteCookingMemory(recipeID: Int, entryID: Int, expectedAccount: OfflineAccount) async throws {
        try requireOfflineAccount(expectedAccount)
        let _: ExperienceAcknowledgement = try await send(
            "/api/recipes/\(recipeID)/cooking-memory/\(entryID)", method: "DELETE"
        )
    }

    func cancelPendingCookingMemory(recipeID: Int, clientEntryID: String, expectedAccount: OfflineAccount) async throws {
        try requireOfflineAccount(expectedAccount)
        let _: ExperienceAcknowledgement = try await send(
            "/api/recipes/\(recipeID)/cooking-memory/client/\(clientEntryID)", method: "DELETE"
        )
    }

    func importReview(recipeID: Int, expectedAccount: OfflineAccount) async throws -> ImportReviewResponse {
        try requireOfflineAccount(expectedAccount)
        return try await send("/api/recipes/\(recipeID)/import-review")
    }

    func saveImportReview(recipeID: Int, request: ImportReviewRequest, expectedAccount: OfflineAccount) async throws -> ImportReviewSavedResponse {
        try requireOfflineAccount(expectedAccount)
        return try await send("/api/recipes/\(recipeID)/import-review", method: "POST", body: request)
    }

    func applyImportCorrection(recipeID: Int, correctionID: Int, expectedAccount: OfflineAccount) async throws -> ImportReviewSavedResponse {
        try requireOfflineAccount(expectedAccount)
        return try await send(
            "/api/recipes/\(recipeID)/import-review/\(correctionID)/apply",
            method: "POST", body: ExperienceEmptyBody()
        )
    }

    func withdrawImportCorrection(recipeID: Int, correctionID: Int, expectedAccount: OfflineAccount) async throws {
        try requireOfflineAccount(expectedAccount)
        let _: ExperienceAcknowledgement = try await send(
            "/api/recipes/\(recipeID)/import-review/\(correctionID)", method: "DELETE"
        )
    }
}

private struct ExperienceEmptyBody: Codable {}
private struct ExperienceAcknowledgement: Decodable { let ok: Bool }
