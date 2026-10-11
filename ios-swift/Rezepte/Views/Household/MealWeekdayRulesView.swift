import SwiftUI

struct MealWeekdayRulesView: View {
    @Binding var rules: [MealWeekdayRule]
    @Binding var additionalCourses: [Int: Set<MealExtraCourse>]
    let count: Int

    var body: some View {
        Section {
            ForEach(1...7, id: \.self) { weekday in
                let rule = rules.first { $0.weekday == weekday }
                NavigationLink {
                    MealWeekdayRuleEditor(weekday: weekday, rules: $rules, additionalCourses: $additionalCourses)
                } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(MealWeekdayRule.title(weekday))
                        Text(rule?.summary ?? "Keine feste Vorgabe").font(.caption).foregroundStyle(.secondary)
                        let courses = MealExtraCourse.allCases.filter { additionalCourses[weekday]?.contains($0) == true }
                        if !courses.isEmpty {
                            Text(courses.map(\.title).joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            if rules.count > count { Text("Für \(rules.count) feste Tage werden mindestens \(rules.count) Gerichte benötigt.").font(.caption).foregroundStyle(.red) }
        } header: { Text("Optionen pro Wochentag") } footer: {
            Text("Alle Tagesvorgaben sind optional. Frühstück, Vorspeise und Nachspeise werden zusätzlich vorgeschlagen. Feste Hauptgerichte werden immer eingeplant. Schon geplante Gerichte bleiben erhalten.")
        }
    }
}

private struct MealWeekdayRuleEditor: View {
    @EnvironmentObject private var session: SessionStore
    let weekday: Int
    @Binding var rules: [MealWeekdayRule]
    @Binding var additionalCourses: [Int: Set<MealExtraCourse>]
    @State private var courses: Set<MealExtraCourse> = []
    @State private var enabled = false
    @State private var category = ""
    @State private var vegetarian = false
    @State private var minimum = 0
    @State private var maximum = 0
    @State private var loaded = false

    private var rule: MealWeekdayRule {
        MealWeekdayRule(weekday: weekday, category: MealIngredientCategory(rawValue: category),
                        minMinutes: minimum == 0 ? nil : minimum, maxMinutes: maximum == 0 ? nil : maximum,
                        vegetarian: vegetarian)
    }

    var body: some View {
        Form {
            Section { Toggle("Hauptgericht für diesen Tag festlegen", isOn: $enabled) }
            if enabled {
                Section("Vorgaben") {
                    Picker("Gericht oder Zutatenart", selection: $category) {
                        Text("Egal").tag("")
                        if session.supports("meal-plan-dish-types-v1") {
                            Section("Gerichtstyp") {
                                ForEach(MealIngredientCategory.dishTypes) { Text($0.title).tag($0.rawValue) }
                            }
                        }
                        Section("Zutatenart") {
                            ForEach(MealIngredientCategory.ingredientTypes) { Text($0.title).tag($0.rawValue) }
                        }
                    }
                    Toggle("Vegetarisch", isOn: $vegetarian)
                    timePicker("Mindestens", value: $minimum)
                    timePicker("Höchstens", value: $maximum)
                    if let message = rule.validationMessage { Text(message).font(.caption).foregroundStyle(.red) }
                }
                Section {
                    Text("Die gewählte Gericht- oder Zutatenart gilt für diesen Tag. Salat, Suppe, Eintopf, Auflauf, Pizza und Wraps stehen auf aktuellen Servern zusätzlich zur Auswahl. Die Mindestzahlen der Woche gelten weiterhin.")
                        .font(.caption).foregroundStyle(.secondary)
                    Text("Zeiten beziehen sich auf die im Rezept hinterlegte Gesamtzeit. Bei Zeitvorgaben werden Rezepte ohne bekannte Zeit ausgeschlossen.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            if session.supports("meal-plan-extra-courses-v1") {
                Section("Zusätzlich vorschlagen · optional") {
                    ForEach(MealExtraCourse.allCases) { course in
                        Toggle(course.title, isOn: Binding(
                            get: { courses.contains(course) },
                            set: { if $0 { courses.insert(course) } else { courses.remove(course) } }
                        ))
                    }
                    Text("Jede aktivierte Mahlzeit erhält einen eigenen Rezeptvorschlag für diesen Tag. Ohne Aktivierung wird nichts zusätzlich eingeplant.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle(MealWeekdayRule.title(weekday))
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            guard !loaded else { return }
            if let existing = rules.first(where: { $0.weekday == weekday }) {
                enabled = true; category = existing.category?.rawValue ?? ""; vegetarian = existing.vegetarian
                minimum = existing.minMinutes ?? 0; maximum = existing.maxMinutes ?? 0
            }
            courses = additionalCourses[weekday] ?? []
            loaded = true
        }
        .onChange(of: enabled) { _, _ in update() }
        .onChange(of: rule) { _, _ in update() }
        .onChange(of: courses) { _, _ in update() }
    }

    private func timePicker(_ title: String, value: Binding<Int>) -> some View {
        let options = Array(Set([15, 20, 30, 45, 60, 90, 120, 180, value.wrappedValue]).subtracting([0])).sorted()
        return Picker(title, selection: value) {
            Text("Egal").tag(0)
            ForEach(options, id: \.self) { Text("\($0) Min.").tag($0) }
        }
    }

    private func update() {
        guard loaded else { return }
        rules.removeAll { $0.weekday == weekday }
        if enabled { rules.append(rule); rules.sort { $0.weekday < $1.weekday } }
        if courses.isEmpty { additionalCourses.removeValue(forKey: weekday) }
        else { additionalCourses[weekday] = courses }
    }
}
