import SwiftUI

@MainActor
struct MealPlanSuggestionsView: View {
    let week: MealWeek
    var onApplied: () async -> Void = {}
    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @StateObject private var state = HouseholdFeatureState()
    @State private var count = 4
    @State private var vegetarian = 2
    @State private var minutes = 0
    @State private var rows: [MealSuggestionDraft] = []
    @State private var warnings: [String] = []
    @State private var constraints = MealPlanConstraints()
    @State private var categories: [Int: Set<MealIngredientCategory>] = [:]
    @State private var showingProfiles = false
    @State private var pdfSnapshot: MealPlanPDFSnapshot?
    private var started: Bool { rows.contains { $0.status != .pending } }
    private var complete: Bool { !rows.isEmpty && rows.allSatisfy { $0.status == .saved } }
    private var excluded: [Int] { week.days.flatMap(\.items).map(\.recipeId) + rows.map(\.id) }
    private var advanced: Bool { session.supports("meal-plan-constraints-v1") }
    private var supportsWeekdays: Bool { advanced && session.supports("meal-plan-weekday-rules-v1") }
    private var planningPreferences: MealPlanningPreferences {
        MealPlanningPreferences(count: count, vegetarian: vegetarian, maxMinutes: minutes, constraints: constraints)
    }
    private var validationMessage: String? {
        planningPreferences.availabilityMessage(advanced: advanced, weekdayRules: supportsWeekdays)
    }
    private var unmetRules: [MealWeekdayRule] {
        MealWeekdayDates.unmetRules(constraints.weekdayRules, rows: rows, categories: categories, weekStart: week.weekStart)
    }
    private var quotasSatisfied: Bool {
        !advanced || constraints.quotas.allSatisfy { quota in
            constraints.fulfilled(quota.category, recipeIDs: rows.map(\.id), categories: categories) == quota.count
        }
    }
    private var partial: Bool { rows.count < count || rows.filter { $0.suggestion.vegetarian }.count < vegetarian || !quotasSatisfied || !unmetRules.isEmpty }

    var body: some View {
        NavigationStack {
            Form {
                if !session.readOnly, session.role != .guest, state.identity == session.identity {
                    Section {
                        Button("Wochenprofile", systemImage: "bookmark") { showingProfiles = true }
                            .disabled(state.busy || started)
                    }
                    Section("Vorschläge aus deiner Sammlung") {
                        Text("Woche ab \(week.weekStart). Prüfe Gerichte, Tage und Portionen vor dem Übernehmen.")
                            .font(.subheadline).foregroundStyle(.secondary)
                        Stepper("\(count) Gerichte", value: $count, in: 1...7)
                            .onChange(of: count) { _, value in
                                vegetarian = min(vegetarian, value)
                                constraints.counts = constraints.counts.mapValues { min($0, value) }
                                clearPreview()
                            }
                        Stepper("Davon mindestens \(vegetarian) vegetarisch", value: $vegetarian, in: 0...count)
                            .onChange(of: vegetarian) { _, _ in clearPreview() }
                        Picker("Zeit pro Gericht", selection: $minutes) {
                            Text("Egal").tag(0)
                            ForEach(Array(Set([15, 20, 30, 45, 60, minutes]).subtracting([0])).sorted(), id: \.self) { Text("\($0) Min.").tag($0) }
                        }.onChange(of: minutes) { _, _ in clearPreview() }
                        Text("Vegetarisch laut Rezeptkennzeichnung. Mit Zeitgrenze werden nur Rezepte mit bekannter Gesamtzeit berücksichtigt.")
                            .font(.caption).foregroundStyle(.secondary)
                    }.disabled(state.busy || started)
                    if advanced {
                        Section("So soll deine Woche aussehen") {
                            Button("7 Gerichte: 2 × Pasta, 4 × Kartoffeln") {
                                count = 7
                                constraints.counts = [.pasta: 2, .potato: 4]
                            }
                            ForEach(MealIngredientCategory.allCases) { category in
                                Picker(category.title, selection: Binding(
                                    get: { constraints.counts[category] ?? -1 },
                                    set: { if $0 < 0 { constraints.counts.removeValue(forKey: category) } else { constraints.counts[category] = $0 } }
                                )) {
                                    Text("Egal").tag(-1)
                                    Text("Keine Gerichte").tag(0)
                                    ForEach(1...count, id: \.self) { Text("Genau \($0) Gerichte").tag($0) }
                                }
                            }
                            Text("Die Anzahl gilt für die neuen Vorschläge. Ein Gericht mit mehreren Zutatenarten zählt bei jeder passenden Vorgabe. Bereits geplante Gerichte bleiben erhalten.")
                                .font(.caption).foregroundStyle(.secondary)
                        }.disabled(state.busy || started)
                        Section("Zutaten und Abwechslung") {
                            TextField("Zutaten ausschließen, z. B. Pilze, Sellerie", text: $constraints.excludedIngredientsText, axis: .vertical)
                                .lineLimit(2...4)
                            Text("Mehrere Zutaten durch Kommas trennen. Verwendet werden die gespeicherten Zutaten der Rezepte.")
                                .font(.caption).foregroundStyle(.secondary)
                            Picker("Bereits geplante Gerichte vermeiden", selection: $constraints.avoidRecentDays) {
                                Text("Ausgeschaltet").tag(0)
                                Text("7 Tage vor dieser Woche").tag(7)
                                Text("14 Tage vor dieser Woche").tag(14)
                                Text("28 Tage vor dieser Woche").tag(28)
                            }
                            if let message = constraints.validationMessage { Text(message).font(.caption).foregroundStyle(.red) }
                        }.disabled(state.busy || started)
                    }
                    if supportsWeekdays {
                        MealWeekdayRulesView(rules: $constraints.weekdayRules, count: count)
                            .disabled(state.busy || started)
                    }
                    Section {
                        Button(rows.isEmpty ? "Vorschläge suchen" : "Andere Vorschläge suchen") { Task { await generate() } }
                            .disabled(state.busy || (started && !complete) || validationMessage != nil)
                        if let validationMessage { Text(validationMessage).font(.caption).foregroundStyle(.red) }
                        HouseholdFeedback(state: state)
                        ForEach(Array(warnings.enumerated()), id: \.offset) { _, warning in Text(warning).font(.subheadline).foregroundStyle(.secondary) }
                    }
                    if !rows.isEmpty, !constraints.weekdayRules.isEmpty {
                        Section("Feste Tage im Ergebnis") {
                            ForEach(constraints.weekdayRules) { rule in
                                let fulfilled = !unmetRules.contains { $0.weekday == rule.weekday }
                                Label("\(MealWeekdayRule.title(rule.weekday)): \(rule.summary)", systemImage: fulfilled ? "checkmark.circle" : "exclamationmark.circle")
                                    .foregroundStyle(fulfilled ? Color.primary : Color.orange)
                            }
                            if !unmetRules.isEmpty { Text("Nicht alle festen Tage sind erfüllt. Suche erneut oder übernimm die unvollständige Auswahl ausdrücklich.").font(.caption).foregroundStyle(.secondary) }
                        }
                    }
                    if advanced, !rows.isEmpty, !constraints.quotas.isEmpty {
                        Section("Deine Vorgaben im Ergebnis") {
                            ForEach(constraints.quotas, id: \.category) { quota in
                                let actual = constraints.fulfilled(quota.category, recipeIDs: rows.map(\.id), categories: categories)
                                Label("\(quota.category.title): \(actual) von genau \(quota.count)",
                                      systemImage: actual == quota.count ? "checkmark.circle" : "exclamationmark.circle")
                                    .foregroundStyle(actual == quota.count ? Color.primary : Color.orange)
                            }
                            if partial { Text("Die Auswahl erfüllt noch nicht alle Vorgaben. Du kannst weiter suchen oder diese Auswahl ausdrücklich übernehmen.").font(.caption).foregroundStyle(.secondary) }
                        }
                    }
                    ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                        Section {
                            NavigationLink(row.suggestion.name) { RecipeDetailView(recipeID: row.id) }.font(.headline)
                            Text([row.suggestion.vegetarian ? "Vegetarisch" : nil,
                                  row.suggestion.totalMinutes.map { "\(Int($0)) Min." } ?? "Zeit nicht hinterlegt"].compactMap { $0 }.joined(separator: " · "))
                                .font(.caption).foregroundStyle(.secondary)
                            Picker("Tag", selection: $rows[index].date) {
                                ForEach(availableDays(for: row)) { day in Text("\(day.label), \(day.date)").tag(day.date) }
                            }.disabled(state.busy || started || rule(for: row) != nil)
                            if rule(for: row) != nil { Text("Der Tag bleibt wegen deiner Wochentagsregel fest.").font(.caption).foregroundStyle(.secondary) }
                            Stepper("\(row.servings) Portionen", value: $rows[index].servings, in: 1...24)
                                .disabled(state.busy || started)
                            if !started {
                                Button("Gericht austauschen", systemImage: "arrow.triangle.2.circlepath") { Task { await replace(index) } }
                                    .disabled(state.busy)
                                Button("Vorschlag entfernen", role: .destructive) { rows.remove(at: index) }.disabled(state.busy)
                            }
                            if row.status == .saved { Label("Im Wochenplan", systemImage: "checkmark.circle.fill") }
                            if row.status == .uncertain { Text("Bestätigung ausstehend. Beim Fortsetzen wird dasselbe Gericht mit demselben Tag und denselben Portionen erneut bestätigt.").font(.caption) }
                        }
                    }
                    if !rows.isEmpty {
                        Section {
                            if complete { Label("Alle Gerichte sind im Wochenplan", systemImage: "checkmark.circle.fill") }
                            else {
                                Button(started ? "Rest übernehmen" : partial ? "\(rows.count) Gerichte dennoch übernehmen" : "Vorschläge übernehmen") { Task { await apply() } }
                                    .buttonStyle(.borderedProminent).disabled(state.busy)
                            }
                        }
                    }
                }
            }
            .navigationTitle("Woche vorschlagen")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Fertig") { dismiss() }.disabled(state.busy) } }
            .interactiveDismissDisabled(state.busy)
            .sheet(isPresented: $showingProfiles) {
                MealPlanningProfilesView(preferences: planningPreferences) { selected in
                    guard !state.busy, !started, state.identity == session.identity else { return }
                    count = selected.count; vegetarian = selected.vegetarian; minutes = selected.maxMinutes
                    constraints = selected.constraints
                    clearPreview()
                }
            }
            .sheet(item: $pdfSnapshot) { snapshot in MealPlanPDFExportSheet(snapshot: snapshot) }
            .toolbar {
                if session.supports("meal-plan-pdf-v1"), !rows.isEmpty {
                    ToolbarItem(placement: .topBarLeading) {
                        Button("PDF", systemImage: "square.and.arrow.up") {
                            pdfSnapshot = MealPlanPDFSnapshot(preferences: planningPreferences, weekStart: week.weekStart,
                                rows: rows, excluding: week.days.flatMap(\.items).map(\.recipeId))
                        }.disabled(state.busy)
                    }
                }
            }
            .task(id: "\(session.identity)-\(week.weekStart)") {
                state.reset(session); rows = []; warnings = []; categories = [:]; constraints = MealPlanConstraints()
                count = 4; vegetarian = 2; minutes = 0; showingProfiles = false; pdfSnapshot = nil
            }
            .onChange(of: constraints) { _, _ in clearPreview() }
        }
    }

    private func clearPreview() {
        guard !started, !state.busy else { return }
        rows = []; warnings = []; categories = [:]; state.error = nil; state.notice = nil
    }

    private func generate() async {
        if let message = validationMessage { state.error = message; return }
        guard !started || complete, let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        do {
            if advanced {
                let result = try await session.api.constrainedMealSuggestions(count: count, vegetarian: vegetarian,
                    maxMinutes: minutes == 0 ? nil : minutes, excluding: excluded,
                    quotas: constraints.quotas, excludedIngredients: constraints.excludedIngredients,
                    avoidRecentDays: constraints.avoidRecentDays, weekStart: week.weekStart,
                    weekdayRules: supportsWeekdays ? constraints.weekdayRules : [])
                guard state.current(token, session) else { return }
                rows = try MealWeekdayDates.drafts(result.items, week: week, requireWeekdays: !constraints.weekdayRules.isEmpty)
                categories = Dictionary(result.items.map { ($0.recipe.id, $0.categories) }, uniquingKeysWith: { first, _ in first })
                warnings = result.warnings
                if !result.searchComplete { state.notice = "Die Suche wurde begrenzt. Weitere passende Kombinationen können noch vorhanden sein." }
            } else {
                let result = try await session.api.mealSuggestions(count: count, vegetarian: vegetarian, maxMinutes: minutes == 0 ? nil : minutes, excluding: excluded)
                guard state.current(token, session) else { return }
                rows = MealSuggestionDraft.make(result.items, days: week.days); warnings = result.warnings
            }
            if rows.isEmpty { state.notice = "Keine passenden Rezepte gefunden. Passe die Vorgaben an und suche erneut." }
        } catch { state.failed(error, token: token, session: session) }
    }

    private func replace(_ index: Int) async {
        guard !started, rows.indices.contains(index), let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        do {
            if advanced {
                let otherRows = rows.enumerated().filter { $0.offset != index }.map(\.element)
                guard let quotas = constraints.replacementQuotas(remainingIDs: otherRows.map(\.id), categories: categories) else {
                    state.notice = "Die fehlenden Vorgaben benötigen mehrere Gerichte. Suche bitte ein neues Vorschlagspaket."
                    return
                }
                let result = try await session.api.constrainedMealSuggestions(count: 1,
                    vegetarian: min(1, max(0, vegetarian - otherRows.filter { $0.suggestion.vegetarian }.count)),
                    maxMinutes: minutes == 0 ? nil : minutes, excluding: excluded,
                    quotas: quotas, excludedIngredients: constraints.excludedIngredients,
                    avoidRecentDays: constraints.avoidRecentDays, weekStart: week.weekStart,
                    weekdayRules: rule(for: rows[index]).map { [$0] } ?? [])
                guard state.current(token, session), rows.indices.contains(index) else { return }
                if result.constraintsSatisfied, let replacement = result.items.first,
                   rule(for: rows[index]).map({ replacement.weekday == $0.weekday && $0.matches(replacement.recipe, categories: replacement.categories) }) ?? true {
                    categories.removeValue(forKey: rows[index].id)
                    rows[index].suggestion = replacement.recipe
                    categories[replacement.recipe.id] = replacement.categories
                } else { state.notice = "Keine weitere passende Alternative gefunden. Der bisherige Vorschlag bleibt erhalten." }
                warnings = result.warnings
            } else {
                let result = try await session.api.mealSuggestions(count: 1, vegetarian: rows[index].suggestion.vegetarian ? 1 : 0,
                                                                   maxMinutes: minutes == 0 ? nil : minutes, excluding: excluded)
                guard state.current(token, session), rows.indices.contains(index) else { return }
                if let replacement = result.items.first { rows[index].suggestion = replacement }
                else { state.notice = "Keine weitere passende Alternative gefunden. Der bisherige Vorschlag bleibt erhalten." }
                warnings = result.warnings
            }
        } catch { state.failed(error, token: token, session: session) }
    }

    private func rule(for row: MealSuggestionDraft) -> MealWeekdayRule? {
        guard let weekday = MealWeekdayDates.weekday(date: row.date, weekStart: week.weekStart) else { return nil }
        return constraints.weekdayRules.first { $0.weekday == weekday }
    }

    private func availableDays(for row: MealSuggestionDraft) -> [MealDay] {
        if rule(for: row) != nil { return week.days.filter { $0.date == row.date } }
        let fixed = Set(constraints.weekdayRules.compactMap { MealWeekdayDates.date(weekday: $0.weekday, weekStart: week.weekStart) })
        return week.days.filter { !fixed.contains($0.date) }
    }

    private func apply() async {
        guard !complete, !rows.isEmpty, let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        do {
            for index in rows.indices {
                guard state.current(token, session) else { return }
                if rows[index].status == .saved { continue }
                let row = rows[index]
                rows[index].status = .uncertain
                // Server upserts the household/date/recipe tuple. Never alter it after an uncertain response.
                _ = try await session.api.addMeal(date: row.date, recipeID: row.id, servings: row.servings)
                guard state.current(token, session) else { return }
                rows[index].status = .saved
            }
            state.notice = "Alle Gerichte wurden übernommen."
        } catch {
            state.failed(error, token: token, session: session)
            if state.current(token, session) { state.error = "\(error.localizedDescription) Bereits bestätigte Gerichte bleiben gespeichert. Mit „Rest übernehmen“ werden nur die übrigen Einträge erneut gesendet." }
        }
        if state.current(token, session) { await onApplied() }
    }
}
