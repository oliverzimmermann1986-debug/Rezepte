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
    private var started: Bool { rows.contains { $0.status != .pending } }
    private var complete: Bool { !rows.isEmpty && rows.allSatisfy { $0.status == .saved } }
    private var excluded: [Int] { week.days.flatMap(\.items).map(\.recipeId) + rows.map(\.id) }

    var body: some View {
        NavigationStack {
            Form {
                if !session.readOnly, session.role != .guest, state.identity == session.identity {
                    Section("Vorschläge aus deiner Sammlung") {
                        Text("Woche ab \(week.weekStart). Prüfe Gerichte, Tage und Portionen vor dem Übernehmen.")
                            .font(.subheadline).foregroundStyle(.secondary)
                        Stepper("\(count) Gerichte", value: $count, in: 1...7)
                            .onChange(of: count) { _, value in vegetarian = min(vegetarian, value); clearPreview() }
                        Stepper("Davon mindestens \(vegetarian) vegetarisch", value: $vegetarian, in: 0...count)
                            .onChange(of: vegetarian) { _, _ in clearPreview() }
                        Picker("Zeit pro Gericht", selection: $minutes) {
                            Text("Egal").tag(0)
                            ForEach([15, 30, 45, 60], id: \.self) { Text("\($0) Min.").tag($0) }
                        }.onChange(of: minutes) { _, _ in clearPreview() }
                        Text("Vegetarisch laut Rezeptkennzeichnung. Mit Zeitgrenze werden nur Rezepte mit bekannter Gesamtzeit berücksichtigt.")
                            .font(.caption).foregroundStyle(.secondary)
                    }.disabled(state.busy || started)
                    Section {
                        Button(rows.isEmpty ? "Vorschläge suchen" : "Andere Vorschläge suchen") { Task { await generate() } }
                            .disabled(state.busy || (started && !complete))
                        HouseholdFeedback(state: state)
                        ForEach(Array(warnings.enumerated()), id: \.offset) { _, warning in Text(warning).font(.subheadline).foregroundStyle(.secondary) }
                    }
                    ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                        Section {
                            NavigationLink(row.suggestion.name) { RecipeDetailView(recipeID: row.id) }.font(.headline)
                            Text([row.suggestion.vegetarian ? "Vegetarisch" : nil,
                                  row.suggestion.totalMinutes.map { "\(Int($0)) Min." } ?? "Zeit nicht hinterlegt"].compactMap { $0 }.joined(separator: " · "))
                                .font(.caption).foregroundStyle(.secondary)
                            Picker("Tag", selection: $rows[index].date) {
                                ForEach(week.days) { day in Text("\(day.label), \(day.date)").tag(day.date) }
                            }.disabled(state.busy || started)
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
                                Button(started ? "Rest übernehmen" : "Vorschläge übernehmen") { Task { await apply() } }
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
            .task(id: "\(session.identity)-\(week.weekStart)") {
                state.reset(session); rows = []; warnings = []
            }
        }
    }

    private func clearPreview() {
        guard !started, !state.busy else { return }
        rows = []; warnings = []; state.error = nil; state.notice = nil
    }

    private func generate() async {
        guard !started || complete, let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        do {
            let result = try await session.api.mealSuggestions(count: count, vegetarian: vegetarian, maxMinutes: minutes == 0 ? nil : minutes, excluding: excluded)
            guard state.current(token, session) else { return }
            rows = MealSuggestionDraft.make(result.items, days: week.days); warnings = result.warnings
            if rows.isEmpty { state.notice = "Keine passenden Rezepte. Versuche eine größere Zeitgrenze oder weniger vegetarische Gerichte." }
        } catch { state.failed(error, token: token, session: session) }
    }

    private func replace(_ index: Int) async {
        guard !started, rows.indices.contains(index), let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        do {
            let result = try await session.api.mealSuggestions(count: 1, vegetarian: rows[index].suggestion.vegetarian ? 1 : 0,
                                                               maxMinutes: minutes == 0 ? nil : minutes, excluding: excluded)
            guard state.current(token, session), rows.indices.contains(index) else { return }
            if let replacement = result.items.first { rows[index].suggestion = replacement }
            else { state.notice = "Keine weitere passende Alternative gefunden. Der bisherige Vorschlag bleibt erhalten." }
            warnings = result.warnings
        } catch { state.failed(error, token: token, session: session) }
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
