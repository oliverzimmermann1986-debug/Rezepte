import SwiftUI

struct MealPlanPDFExportSheet: View {
    let snapshot: MealPlanPDFSnapshot
    @EnvironmentObject private var session: SessionStore

    var body: some View {
        PDFPreviewSheet(title: "Wochenvorschläge \(snapshot.criteria.weekStart)") {
            guard session.supports("meal-plan-pdf-v1"), !session.readOnly, session.role != .guest else {
                throw APIError.server(403, "Der PDF-Export ist hier nicht verfügbar.")
            }
            return try await session.api.mealPlanSuggestionsPDF(snapshot)
        }
    }
}
