import Foundation

struct MealWeekdayRule: Codable, Equatable, Identifiable {
    var weekday: Int
    var category: MealIngredientCategory?
    var minMinutes: Int?
    var maxMinutes: Int?
    var vegetarian = false
    var id: Int { weekday }

    static func title(_ weekday: Int) -> String {
        let names = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
        return (1...7).contains(weekday) ? names[weekday - 1] : "Unbekannter Tag"
    }

    var summary: String {
        var parts: [String] = []
        if let category { parts.append(category.title) }
        if vegetarian { parts.append("vegetarisch") }
        if let minMinutes { parts.append("ab \(minMinutes) Min.") }
        if let maxMinutes { parts.append("bis \(maxMinutes) Min.") }
        return parts.isEmpty ? "Gericht für diesen Tag" : parts.joined(separator: " · ")
    }

    var validationMessage: String? {
        if !(1...7).contains(weekday) { return "Bitte einen gültigen Wochentag wählen." }
        if [minMinutes, maxMinutes].compactMap({ $0 }).contains(where: { !(1...1440).contains($0) }) {
            return "Zeitgrenzen müssen zwischen 1 und 1440 Minuten liegen."
        }
        if let minMinutes, let maxMinutes, minMinutes > maxMinutes { return "Die Mindestzeit darf nicht über der Höchstzeit liegen." }
        return nil
    }

    func matches(_ recipe: MealSuggestion, categories: Set<MealIngredientCategory>) -> Bool {
        if let category, !categories.contains(category) { return false }
        if vegetarian && !recipe.vegetarian { return false }
        if let minMinutes, !(recipe.totalMinutes.map { $0 >= Double(minMinutes) } ?? false) { return false }
        if let maxMinutes, !(recipe.totalMinutes.map { $0 <= Double(maxMinutes) } ?? false) { return false }
        return true
    }
}

/// Portable preferences, without a specific week, recipe selection or account data.
struct MealPlanningPreferences: Codable, Equatable {
    var count = 4
    var vegetarian = 2
    var maxMinutes = 0
    var servings = 2
    var constraints = MealPlanConstraints()

    private enum CodingKeys: String, CodingKey { case count, vegetarian, maxMinutes, servings, constraints }

    init(count: Int = 4, vegetarian: Int = 2, maxMinutes: Int = 0, servings: Int = 2, constraints: MealPlanConstraints = MealPlanConstraints()) {
        self.count = count; self.vegetarian = vegetarian; self.maxMinutes = maxMinutes
        self.servings = servings; self.constraints = constraints
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        count = try values.decodeIfPresent(Int.self, forKey: .count) ?? 4
        vegetarian = try values.decodeIfPresent(Int.self, forKey: .vegetarian) ?? 2
        maxMinutes = try values.decodeIfPresent(Int.self, forKey: .maxMinutes) ?? 0
        servings = try values.decodeIfPresent(Int.self, forKey: .servings) ?? 2
        constraints = try values.decodeIfPresent(MealPlanConstraints.self, forKey: .constraints) ?? MealPlanConstraints()
    }

    var validationMessage: String? {
        if !(1...7).contains(count) { return "Bitte zwischen 1 und 7 Gerichte wählen." }
        if !(1...24).contains(servings) { return "Bitte zwischen 1 und 24 Portionen wählen." }
        if !(0...count).contains(vegetarian) { return "Die vegetarische Mindestanzahl darf die Gerichtanzahl nicht überschreiten." }
        if !(0...1440).contains(maxMinutes) { return "Bitte eine gültige Zeitgrenze wählen." }
        if let message = constraints.validationMessage { return message }
        if constraints.counts.values.contains(where: { $0 > count }) { return "Eine Wochenvorgabe darf die Gerichtanzahl nicht überschreiten." }
        if constraints.weekdayRules.count > count { return "Wähle mindestens so viele Gerichte wie festgelegte Wochentage." }
        for rule in constraints.weekdayRules {
            if maxMinutes > 0, let minimum = rule.minMinutes, minimum > maxMinutes {
                return "\(MealWeekdayRule.title(rule.weekday)): Die Mindestzeit liegt über der Zeitgrenze für die Woche."
            }
        }
        return nil
    }

    func availabilityMessage(advanced: Bool, weekdayRules: Bool, dishTypes: Bool = false, householdFilter: Bool = false,
                             minimumQuotas: Bool = true, extraCourses: Bool = false) -> String? {
        if let validationMessage { return validationMessage }
        if !extraCourses && !constraints.extraCourseSlots.isEmpty {
            return "Dieser Server unterstützt zusätzliche Mahlzeiten pro Tag noch nicht."
        }
        if !minimumQuotas && constraints.counts.values.contains(where: { $0 > 0 }) {
            return "Dieser Server unterstützt Mindestzahlen pro Gerichtstyp noch nicht."
        }
        if !advanced && (!constraints.quotas.isEmpty || !constraints.excludedIngredients.isEmpty || constraints.avoidRecentDays > 0) {
            return "Dieser Server unterstützt die erweiterten Wochenvorgaben noch nicht."
        }
        if constraints.householdOnly && !householdFilter { return "Dieser Server unterstützt die Auswahl nur aus dem Haushalt noch nicht." }
        if !weekdayRules && !constraints.weekdayRules.isEmpty { return "Dieser Server unterstützt Regeln pro Wochentag noch nicht." }
        if !dishTypes && (constraints.counts.keys.contains(where: { $0.isDishType }) || constraints.weekdayRules.contains(where: { $0.category?.isDishType == true })) {
            return "Dieser Server unterstützt Salat, Suppe und weitere Gerichtstypen noch nicht."
        }
        return nil
    }
}

enum MealWeekdayDates {
    static func localDate(_ date: Date, timeZone: TimeZone = .autoupdatingCurrent) -> String {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: date)
    }
    private static func formatter() -> DateFormatter {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.isLenient = false
        return formatter
    }

    static func date(weekday: Int, weekStart: String) -> String? {
        guard (1...7).contains(weekday) else { return nil }
        let formatter = formatter()
        guard let start = formatter.date(from: weekStart), formatter.string(from: start) == weekStart else { return nil }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        guard calendar.component(.weekday, from: start) == 2,
              let date = calendar.date(byAdding: .day, value: weekday - 1, to: start) else { return nil }
        return formatter.string(from: date)
    }

    static func weekday(date: String, weekStart: String) -> Int? {
        (1...7).first { Self.date(weekday: $0, weekStart: weekStart) == date }
    }

    static func drafts(_ items: [ConstrainedMealSuggestion], week: MealWeek, requireWeekdays: Bool) throws -> [MealSuggestionDraft] {
        guard requireWeekdays else { return MealSuggestionDraft.make(items.map(\.recipe), days: week.days) }
        var seenDates = Set<String>()
        var seenRecipes = Set<Int>()
        return try items.map { item in
            guard let weekday = item.weekday, let date = date(weekday: weekday, weekStart: week.weekStart),
                  week.days.contains(where: { $0.date == date }), seenDates.insert(date).inserted,
                  seenRecipes.insert(item.recipe.id).inserted else {
                throw APIError.invalidResponse("Die vorgeschlagenen Wochentage sind ungültig. Bitte erneut suchen.")
            }
            return MealSuggestionDraft(suggestion: item.recipe, date: date, servings: min(24, max(1, item.recipe.servings ?? 2)))
        }
    }

    static func unmetRules(_ rules: [MealWeekdayRule], rows: [MealSuggestionDraft], categories: [Int: Set<MealIngredientCategory>], weekStart: String) -> [MealWeekdayRule] {
        rules.filter { rule in
            guard let date = date(weekday: rule.weekday, weekStart: weekStart) else { return true }
            let assigned = rows.filter { $0.date == date }
            return assigned.isEmpty || !assigned.allSatisfy { rule.matches($0.suggestion, categories: categories[$0.id] ?? []) }
        }
    }
}
