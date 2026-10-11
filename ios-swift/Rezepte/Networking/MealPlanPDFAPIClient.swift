import Foundation

extension APIClient {
    func mealPlanSuggestionsPDF(_ snapshot: MealPlanPDFSnapshot) async throws -> Data {
        let data = try await kitchenDownload("/api/discovery/meal-plan/pdf", method: "POST", body: snapshot, accept: "application/pdf")
        guard data.starts(with: Data("%PDF-".utf8)), data.count <= 2 * 1024 * 1024 else {
            throw APIError.invalidResponse("Wochenvorschläge-PDF")
        }
        return data
    }
}
