import Foundation

enum MealIngredientCategory: String, Codable, CaseIterable, Identifiable {
    case pasta, potato, rice, legume, salad, soup, stew, casserole, pizza, wrap
    var id: String { rawValue }
    var title: String {
        switch self {
        case .pasta: return "Pasta"
        case .potato: return "Kartoffeln"
        case .rice: return "Reis"
        case .legume: return "Hülsenfrüchte"
        case .salad: return "Salat"
        case .soup: return "Suppe"
        case .stew: return "Eintopf"
        case .casserole: return "Auflauf"
        case .pizza: return "Pizza"
        case .wrap: return "Wraps"
        }
    }
    var isDishType: Bool { Self.dishTypes.contains(self) }
    static var ingredientTypes: [Self] { [.pasta, .potato, .rice, .legume] }
    static var dishTypes: [Self] { [.salad, .soup, .stew, .casserole, .pizza, .wrap] }
    static func available(dishTypes: Bool) -> [Self] { dishTypes ? allCases : ingredientTypes }
}

struct MealCategoryQuota: Codable, Equatable {
    let category: MealIngredientCategory
    let count: Int
}

enum MealExtraCourse: String, Codable, CaseIterable, Identifiable {
    case breakfast, starter, dessert
    var id: String { rawValue }
    var title: String {
        switch self {
        case .breakfast: "Frühstück"
        case .starter: "Vorspeise"
        case .dessert: "Nachspeise"
        }
    }
}

struct MealExtraCourseSlot: Encodable {
    let weekday: Int
    let course: MealExtraCourse
}

struct MealPlanConstraints: Codable, Equatable {
    var counts: [MealIngredientCategory: Int] = [:]
    var excludedIngredientsText = ""
    var avoidRecentDays = 0
    var weekdayRules: [MealWeekdayRule] = []
    var householdOnly = false
    var additionalCourses: [Int: Set<MealExtraCourse>] = [:]

    var extraCourseSlots: [MealExtraCourseSlot] {
        additionalCourses.keys.sorted().flatMap { day in
            MealExtraCourse.allCases.filter { additionalCourses[day]?.contains($0) == true }
                .map { MealExtraCourseSlot(weekday: day, course: $0) }
        }
    }

    private enum CodingKeys: String, CodingKey { case counts, excludedIngredientsText, avoidRecentDays, weekdayRules, householdOnly, additionalCourses }

    init(counts: [MealIngredientCategory: Int] = [:], excludedIngredientsText: String = "",
         avoidRecentDays: Int = 0, weekdayRules: [MealWeekdayRule] = [], householdOnly: Bool = false,
         additionalCourses: [Int: Set<MealExtraCourse>] = [:]) {
        self.counts = counts
        self.excludedIngredientsText = excludedIngredientsText
        self.avoidRecentDays = avoidRecentDays
        self.weekdayRules = weekdayRules
        self.householdOnly = householdOnly
        self.additionalCourses = additionalCourses
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        counts = try values.decodeIfPresent([MealIngredientCategory: Int].self, forKey: .counts) ?? [:]
        excludedIngredientsText = try values.decodeIfPresent(String.self, forKey: .excludedIngredientsText) ?? ""
        avoidRecentDays = try values.decodeIfPresent(Int.self, forKey: .avoidRecentDays) ?? 0
        weekdayRules = try values.decodeIfPresent([MealWeekdayRule].self, forKey: .weekdayRules) ?? []
        householdOnly = try values.decodeIfPresent(Bool.self, forKey: .householdOnly) ?? false
        additionalCourses = try values.decodeIfPresent([Int: Set<MealExtraCourse>].self, forKey: .additionalCourses) ?? [:]
    }

    var quotas: [MealCategoryQuota] {
        MealIngredientCategory.allCases.compactMap { category in
            counts[category].map { MealCategoryQuota(category: category, count: $0) }
        }
    }

    var excludedIngredients: [String] {
        var seen = Set<String>()
        return excludedIngredientsText.components(separatedBy: CharacterSet(charactersIn: ",;\n"))
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty && seen.insert($0.lowercased()).inserted }
    }

    var validationMessage: String? {
        if additionalCourses.keys.contains(where: { !(1...7).contains($0) }) { return "Bitte einen gültigen Wochentag für zusätzliche Mahlzeiten wählen." }
        if ![0, 7, 14, 28].contains(avoidRecentDays) { return "Bitte einen gültigen Zeitraum für die Abwechslung wählen." }
        if counts.values.contains(where: { !(0...7).contains($0) }) { return "Die Anzahl je Vorgabe muss zwischen 0 und 7 liegen." }
        if weekdayRules.count > 7 || Set(weekdayRules.map(\.weekday)).count != weekdayRules.count { return "Bitte jeden Wochentag nur einmal festlegen." }
        if let message = weekdayRules.compactMap(\.validationMessage).first { return message }
        if excludedIngredients.count > 100 { return "Bitte höchstens 100 Zutaten ausschließen." }
        if excludedIngredients.contains(where: { $0.count > 200 }) { return "Eine ausgeschlossene Zutat darf höchstens 200 Zeichen enthalten." }
        return nil
    }

    func fulfilled(_ category: MealIngredientCategory, recipeIDs: [Int], categories: [Int: Set<MealIngredientCategory>]) -> Int {
        Set(recipeIDs).filter { categories[$0]?.contains(category) == true }.count
    }

    /// Count each dish in every matching family; overlapping quotas need no sum limit.
    func replacementQuotas(remainingIDs: [Int], categories: [Int: Set<MealIngredientCategory>]) -> [MealCategoryQuota]? {
        var result: [MealCategoryQuota] = []
        for quota in quotas {
            let actual = fulfilled(quota.category, recipeIDs: remainingIDs, categories: categories)
            if quota.count == 0 {
                guard actual == 0 else { return nil }
                result.append(quota)
                continue
            }
            let remaining = max(0, quota.count - actual)
            guard remaining <= 1 else { return nil }
            if remaining == 0 { continue }
            result.append(MealCategoryQuota(category: quota.category, count: remaining))
        }
        return result
    }
}

struct ConstrainedMealSuggestion: Decodable {
    let recipe: MealSuggestion
    let categories: Set<MealIngredientCategory>
    let weekday: Int?
    private enum CodingKeys: String, CodingKey { case categories, weekday }

    init(from decoder: Decoder) throws {
        recipe = try MealSuggestion(from: decoder)
        let container = try decoder.container(keyedBy: CodingKeys.self)
        categories = Set(try container.decode([String].self, forKey: .categories).compactMap(MealIngredientCategory.init(rawValue:)))
        weekday = try container.decodeIfPresent(Int.self, forKey: .weekday)
    }
}

struct MealQuotaShortfall: Decodable {
    let category: MealIngredientCategory
    let requested: Int
    let fulfilled: Int
    let missing: Int
}

struct ConstrainedMealSuggestionResponse: Decodable {
    let items: [ConstrainedMealSuggestion]
    let warnings: [String]
    let missingQuotas: [MealQuotaShortfall]
    let constraintsSatisfied: Bool
    let searchComplete: Bool
    let missingWeekdays: [Int]

    private enum CodingKeys: String, CodingKey { case items, warnings, missingQuotas, constraintsSatisfied, searchComplete, missingWeekdays }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        items = try values.decode([ConstrainedMealSuggestion].self, forKey: .items)
        warnings = try values.decode([String].self, forKey: .warnings)
        missingQuotas = try values.decode([MealQuotaShortfall].self, forKey: .missingQuotas)
        constraintsSatisfied = try values.decode(Bool.self, forKey: .constraintsSatisfied)
        searchComplete = try values.decode(Bool.self, forKey: .searchComplete)
        missingWeekdays = try values.decodeIfPresent([Int].self, forKey: .missingWeekdays) ?? []
    }
}
