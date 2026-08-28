import Foundation

struct LoginResponse: Codable {
    let token: String
    let username: String
    let expiresIn: Int
    let readOnly: Bool?
}

struct SessionResponse: Codable {
    let username: String
    let fullAccess: Bool?
    let readOnly: Bool?
}

struct RecipeListResponse: Codable {
    let total: Int
    let items: [RecipeSummary]
}

struct RecipeCountResponse: Codable {
    let total: Int
}

@propertyWrapper
struct FlexibleBool: Codable, Hashable {
    var wrappedValue: Bool?

    init(wrappedValue: Bool?) {
        self.wrappedValue = wrappedValue
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            wrappedValue = nil
        } else if let value = try? container.decode(Bool.self) {
            wrappedValue = value
        } else if let value = try? container.decode(Int.self), value == 0 || value == 1 {
            wrappedValue = value == 1
        } else {
            throw DecodingError.typeMismatch(
                Bool.self,
                DecodingError.Context(
                    codingPath: decoder.codingPath,
                    debugDescription: "Erwartet Bool oder SQLite-Bool 0/1"
                )
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        if let wrappedValue {
            try container.encode(wrappedValue)
        } else {
            try container.encodeNil()
        }
    }
}

extension KeyedDecodingContainer {
    func decode(_ type: FlexibleBool.Type, forKey key: Key) throws -> FlexibleBool {
        try decodeIfPresent(type, forKey: key) ?? FlexibleBool(wrappedValue: nil)
    }
}

struct RecipeFilters: Equatable, Sendable {
    var type = ""
    var category = ""
    var tagIDs: Set<Int> = []
    var allergenTagIDs: Set<Int> = []
    var includedIngredients: Set<String> = []
    var excludedIngredients: Set<String> = []
    var favoriteOnly = false
    var minRating = 0
    var manualOnly = false

    var activeCount: Int {
        (type.isEmpty ? 0 : 1)
            + (category.isEmpty ? 0 : 1)
            + tagIDs.union(allergenTagIDs).count
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

    var allergenInfo: AllergenInfo? {
        AllergenInfo(rawValue: name.trimmingCharacters(in: .whitespacesAndNewlines).lowercased())
    }
}

enum AllergenInfo: String, CaseIterable, Sendable {
    case glutenFree = "glutenfrei"
    case lactoseFree = "laktosefrei"
    case eggFree = "eifrei"
    case nutFree = "nussfrei"

    var title: String {
        switch self {
        case .glutenFree: "Glutenfrei"
        case .lactoseFree: "Laktosefrei"
        case .eggFree: "Eifrei"
        case .nutFree: "Nussfrei"
        }
    }

    var systemImage: String {
        switch self {
        case .glutenFree: "leaf"
        case .lactoseFree: "drop"
        case .eggFree: "circle"
        case .nutFree: "shield"
        }
    }

    var sortIndex: Int {
        Self.allCases.firstIndex(of: self) ?? 0
    }
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
    let sourceAddedAt: Double?
    @FlexibleBool var userVerified: Bool?
    let verifiedAt: Double?
    let verifiedBy: String?
    let ingredientsStatus: String?
    let imageGenerationStatus: String?
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
    let sourceAddedAt: Double?
    @FlexibleBool var userVerified: Bool?
    let verifiedAt: Double?
    let verifiedBy: String?
    let ingredientsStatus: String?
    let descriptionOriginal: String?
    let pdfFilename: String?
    let imageGenerationStatus: String?
    let imageGenerationModel: String?
    let imageGenerationPrompt: String?
    let imageGenerationBatchId: String?
    let imageGeneratedAt: Double?
    let imageBackups: [ImageBackup]?
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

struct CookingProgress: Codable, Equatable {
    let recipeId: Int
    let username: String
    let completedSteps: [Int]
    let activeStep: Int
    let servings: Int?
    let startedAt: Double?
    let updatedAt: Double?
    let exists: Bool
    let stepCount: Int
}

struct CookingCompletionResult: Codable {
    let ok: Bool
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
    let category: String?
    let icon: String?

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

struct ShoppingSuggestion: Codable, Identifiable, Hashable {
    let canonicalName: String
    let name: String
    let category: String?
    let icon: String?
    let defaultUnit: String?
    let usageCount: Int?

    var id: String { canonicalName }
}

struct ShoppingSuggestionsResponse: Codable {
    let items: [ShoppingSuggestion]
}

struct ShoppingCategory: Codable, Identifiable, Hashable {
    let name: String
    let icon: String

    var id: String { name }
}

struct ShoppingCategoriesResponse: Codable {
    let items: [ShoppingCategory]
}

struct RecurringCartResponse: Codable {
    let items: [RecurringCartItem]
}

struct RecurringCartItem: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let amount: Double?
    let defaultUnit: String?
    let category: String?
    let icon: String?
    let intervalDays: Int
    let nextDueOn: String
    let dueInDays: Int
    @FlexibleBool var active: Bool?

    var isActive: Bool { active == true }

    var quantityText: String? {
        guard let amount else { return nil }
        let value = amount.rounded() == amount ? String(Int(amount)) : String(format: "%.2f", amount)
        return [value, defaultUnit]
            .compactMap { $0?.nilIfEmpty }
            .joined(separator: " ")
    }

    var intervalText: String {
        intervalDays == 1 ? "Jeden Tag" : "Alle \(intervalDays) Tage"
    }

    var dueText: String {
        guard isActive else { return "Pausiert" }
        switch dueInDays {
        case ...(-1): "Seit \(-dueInDays) Tagen fällig"
        case 0: "Heute fällig"
        case 1: "Morgen fällig"
        default: "In \(dueInDays) Tagen fällig"
        }
    }
}

struct RecurringRunResponse: Codable {
    let ok: Bool
    let count: Int
}

struct ImageBackup: Codable, Identifiable, Hashable {
    let id: Int
    let batchId: String
    let recipeId: Int
    let recipeName: String?
    let originalFilename: String
    let originalSha256: String
    let generatedSha256: String?
    let model: String?
    let prompt: String?
    let createdAt: Double
    let generatedAt: Double?
    let restoredAt: Double?
    let fileUrl: String?
}

struct ImageBackupResponse: Codable {
    let items: [ImageBackup]
}

struct ImageGenerationStart: Codable {
    let ok: Bool
    let taskId: Int
    let recipeId: Int
    let batchId: String
}

struct ImageBackfillStart: Codable {
    let ok: Bool
    let taskId: Int
    let runId: Int
    let batchId: String
}

struct ImageBackfillRun: Codable {
    let id: Int
    let kind: String
    let startedAt: Double
    let endedAt: Double?
    let status: String
    let startedBy: String?
    let result: ImageBackfillResult
}

struct ImageBackfillResult: Codable {
    let ok: Bool?
    let phase: String?
    let batchId: String?
    let total: Int?
    let processed: Int?
    let backedUp: Int?
    let generated: Int?
    let errorCount: Int?
    let errors: [ImageBackfillError]?
    let error: String?
}

struct ImageBackfillError: Codable, Identifiable {
    let recipeId: Int
    let name: String?
    let error: String

    var id: Int { recipeId }
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
    let contentType: String?
    let description: String?
    let status: String?
    let reason: String?
    let aiSuggestion: PendingSuggestion?

    var id: String { url }
    var displayName: String {
        aiSuggestion?.name?.nilIfEmpty
            ?? aiSuggestion?.filename?.nilIfEmpty
            ?? "Unbenannter Import"
    }
}

struct PendingSuggestion: Codable, Hashable {
    let name: String?
    let type: String?
    let category: String?
    let confidence: Double?
    let filename: String?
    let source: String?
    let platform: String?
    let hasThumbnail: Bool?
    let servings: Int?
    let ingredients: [PendingIngredient]?
    let steps: [PendingStep]?
}

struct PendingIngredient: Codable, Hashable {
    let name: String
    let amount: Double?
    let unit: String?
    let raw: String?
}

struct PendingStep: Codable, Hashable {
    let instruction: String
    let timerSeconds: Int?
}

struct PendingAnalysisResult: Codable {
    let ok: Bool
    let action: String?
    let error: String?
    let message: String?
    let description: String?
    let analysis: PendingSuggestion?
}

struct FailedDownload: Codable, Identifiable {
    let url: String
    let firstSeen: Double?
    let lastTry: Double?
    let attempts: Int
    let lastError: String?

    var id: String { url }
}

struct APIResult: Codable {
    let ok: Bool?
    let action: String?
    let message: String?
    let status: String?
    let added: Int?
    let name: String?
    let recipeId: Int?
}
