import SwiftUI

struct MealWeekdayRulesView: View {
    @Binding var rules: [MealWeekdayRule]
    let count: Int

    var body: some View {
        Section {
            ForEach(1...7, id: \.self) { weekday in
                let rule = rules.first { $0.weekday == weekday }
                NavigationLink {
                    MealWeekdayRuleEditor(weekday: weekday, rules: $rules)
                } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(MealWeekdayRule.title(weekday))
                        Text(rule?.summary ?? "Keine feste Vorgabe").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            if rules.count > count { Text("Für \(rules.count) feste Tage werden mindestens \(rules.count) Gerichte benötigt.").font(.caption).foregroundStyle(.red) }
        } header: { Text("Regeln pro Wochentag") } footer: {
            Text("Feste Tage werden immer eingeplant. Weitere Gerichte kommen auf die frühesten übrigen Tage. Zeitgrenzen und Zutatenvorgaben der gesamten Woche gelten zusätzlich. Schon geplante Gerichte bleiben erhalten.")
        }
    }
}

private struct MealWeekdayRuleEditor: View {
    @EnvironmentObject private var session: SessionStore
    let weekday: Int
    @Binding var rules: [MealWeekdayRule]
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
            Section { Toggle("Gericht für diesen Tag einplanen", isOn: $enabled) }
            if enabled {
                Section("Vorgaben") {
                    Picker("Gericht oder Zutatenart", selection: $category) {
                        Text("Egal").tag("")
                        ForEach(MealIngredientCategory.available(dishTypes: session.supports("meal-plan-dish-types-v1"))) { Text($0.title).tag($0.rawValue) }
                    }
                    Toggle("Vegetarisch", isOn: $vegetarian)
                    timePicker("Mindestens", value: $minimum)
                    timePicker("Höchstens", value: $maximum)
                    if let message = rule.validationMessage { Text(message).font(.caption).foregroundStyle(.red) }
                }
                Section { Text("Zeiten beziehen sich auf die im Rezept hinterlegte Gesamtzeit. Bei Zeitvorgaben werden Rezepte ohne bekannte Zeit ausgeschlossen.").font(.caption).foregroundStyle(.secondary) }
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
            loaded = true
        }
        .onChange(of: enabled) { _, _ in update() }
        .onChange(of: rule) { _, _ in update() }
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
    }
}
