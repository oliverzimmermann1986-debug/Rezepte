import Foundation

struct CookingMemoryEntry: Codable, Identifiable, Equatable {
    let id: Int
    let clientEntryId: String
    let recipeId: Int
    let createdAt: Double
    let note: String
    let adjustments: String
    let nextTime: String
    let servings: Int?
    let stepNumber: Int?
    let stepInstruction: String?
    let stepIsCurrent: Bool?

    func matches(stepNumber number: Int, instruction: String) -> Bool {
        stepNumber == number && stepInstruction == instruction && stepIsCurrent != false
    }
}

struct CookingMemoryResponse: Codable {
    let items: [CookingMemoryEntry]
    let total: Int
}

struct CookingMemoryRequest: Codable, Equatable, Identifiable {
    var clientEntryId = UUID().uuidString
    var note = ""
    var adjustments = ""
    var nextTime = ""
    var servings: Int?
    var stepNumber: Int?
    var stepInstruction: String?
    var id: String { clientEntryId }

    var hasContent: Bool {
        [note, adjustments, nextTime].contains { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    }

    var isValid: Bool {
        hasContent && [note, adjustments, nextTime].allSatisfy { $0.count <= 2000 }
    }
}

struct CookingMemorySavedResponse: Decodable {
    let ok: Bool
    let entry: CookingMemoryEntry
}

struct ImportReviewIngredient: Codable, Equatable {
    var name: String
    var amount: Double?
    var unit: String?
    var raw: String?
}

struct ImportReviewStep: Codable, Equatable {
    var instruction: String
    var timerSeconds: Int?
}

struct ImportReviewSource: Codable {
    let url: String?
    let description: String?
}

struct ImportCorrection: Codable, Identifiable {
    let id: Int
    let status: String
    let reason: String?
    let createdAt: Double?
    let username: String
    let before: ImportCorrectionSnapshot
    let proposed: ImportCorrectionSnapshot
}

struct ImportCorrectionSnapshot: Codable {
    let ingredients: [ImportReviewIngredient]
    let steps: [ImportReviewStep]
    let servings: Int?
}

struct ImportReviewResponse: Codable {
    let recipeId: Int
    let revision: String
    let canApply: Bool
    let source: ImportReviewSource
    let ingredients: [ImportReviewIngredient]
    let steps: [ImportReviewStep]
    let servings: Int?
    let quality: RecipeQualityReport
    let corrections: [ImportCorrection]
}

struct ImportReviewRequest: Codable {
    var clientRequestId = UUID().uuidString
    let expectedRevision: String
    let ingredients: [ImportReviewIngredient]
    let steps: [ImportReviewStep]
    let servings: Int?
    let reason: String
}

struct ImportReviewSavedResponse: Decodable {
    let ok: Bool
    let status: String
    let correction: ImportCorrection
    let review: ImportReviewResponse
}

/// Text fields deliberately distinguish an unknown amount from an invalid number.
/// No guessed quantities are introduced when an import is incomplete.
enum ImportReviewNumber {
    static func parse(_ text: String) -> Double? {
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: ",", with: ".")
        guard let number = Double(value), number.isFinite, number >= 0, number <= 1_000_000 else { return nil }
        return number
    }

    static func format(_ value: Double?) -> String {
        guard let value else { return "" }
        if value.isFinite, (0...1_000_000).contains(value), value.rounded() == value { return String(Int(value)) }
        return String(value)
    }
}
