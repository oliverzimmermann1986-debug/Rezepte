import SwiftUI

struct MealPlanningProfilesView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var profiles: MealPlanningProfileStore
    @Environment(\.dismiss) private var dismiss
    let preferences: MealPlanningPreferences
    let onApply: (MealPlanningPreferences) -> Void
    @State private var name = ""
    @State private var renameText = ""
    @State private var renaming: MealPlanningProfile?
    @State private var errorMessage: String?
    @State private var sourceIdentity: UUID?

    var body: some View {
        NavigationStack {
            List {
                Section("Aktuelle Vorgaben speichern") {
                    TextField("Name, z. B. Schnelle Woche", text: $name)
                    Text(summary(preferences)).font(.caption).foregroundStyle(.secondary)
                    Button("Wochenprofil speichern", systemImage: "bookmark") {
                        perform { try profiles.save(name: name, preferences: preferences); name = "" }
                    }
                    .disabled(!profiles.isReady || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || preferences.validationMessage != nil)
                    if let message = preferences.validationMessage { Text(message).font(.caption).foregroundStyle(.red) }
                }
                Section {
                    if profiles.items.isEmpty { Text("Noch keine gespeicherten Wochenprofile.").foregroundStyle(.secondary) }
                    ForEach(profiles.items) { item in
                        let unavailable = item.preferences.availabilityMessage(advanced: session.supports("meal-plan-constraints-v1"), weekdayRules: session.supports("meal-plan-weekday-rules-v1"), dishTypes: session.supports("meal-plan-dish-types-v1"), householdFilter: session.supports("shared-recipes-household-filter-v1"))
                        VStack(alignment: .leading, spacing: 4) {
                            Button {
                                perform {
                                    let selected = try profiles.preferences(id: item.id)
                                    if let message = selected.availabilityMessage(advanced: session.supports("meal-plan-constraints-v1"), weekdayRules: session.supports("meal-plan-weekday-rules-v1"), dishTypes: session.supports("meal-plan-dish-types-v1"), householdFilter: session.supports("shared-recipes-household-filter-v1")) {
                                        throw APIError.server(0, message)
                                    }
                                    onApply(selected); dismiss()
                                }
                            } label: {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(item.name).font(.headline)
                                    Text(summary(item.preferences)).font(.caption).foregroundStyle(.secondary)
                                }
                            }.disabled(!profiles.isReady || unavailable != nil)
                            if let unavailable { Text(unavailable).font(.caption).foregroundStyle(.red) }
                        }
                        .swipeActions(allowsFullSwipe: false) {
                            Button("Löschen", role: .destructive) { perform { try profiles.remove(id: item.id) } }
                            Button("Umbenennen") { renaming = item; renameText = item.name }.tint(.blue)
                        }
                        .contextMenu {
                            Button("Umbenennen") { renaming = item; renameText = item.name }
                            Button("Löschen", role: .destructive) { perform { try profiles.remove(id: item.id) } }
                        }
                    }
                } header: { Text("Deine Wochenprofile") } footer: {
                    Text("Anzahl, Gerichtstypen, Zeitgrenze, Zutaten, Abwechslung und Wochentagsregeln werden auf diesem Gerät für dein Konto und deinen Haushalt gespeichert. Ein Profil übernimmt die Vorgaben; passende Rezepte suchst du anschließend für die ausgewählte Woche.")
                }
                if let message = errorMessage ?? profiles.errorMessage { Section { Text(message).foregroundStyle(.red) } }
            }
            .navigationTitle("Wochenprofile")
            .navigationBarTitleDisplayMode(.inline)
            .onAppear { if sourceIdentity == nil { sourceIdentity = session.identity } }
            .onChange(of: session.identity) { _, _ in dismiss() }
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Schließen") { dismiss() } } }
            .alert("Wochenprofil umbenennen", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
                TextField("Name", text: $renameText)
                Button("Speichern") {
                    if let item = renaming { perform { try profiles.rename(id: item.id, name: renameText) } }
                    renaming = nil
                }
                Button("Abbrechen", role: .cancel) { renaming = nil }
            }
        }
    }

    private func summary(_ value: MealPlanningPreferences) -> String {
        var parts = ["\(value.count) Gerichte", "mind. \(value.vegetarian) vegetarisch"]
        if value.maxMinutes > 0 { parts.append("bis \(value.maxMinutes) Min.") }
        parts.append(contentsOf: value.constraints.quotas.map { "\($0.count) × \($0.category.title)" })
        parts.append(value.constraints.avoidRecentDays == 0
                     ? "Vorwochen erlaubt" : "\(value.constraints.avoidRecentDays / 7) Wochen Rückblick")
        if !value.constraints.weekdayRules.isEmpty { parts.append("\(value.constraints.weekdayRules.count) feste Tage") }
        return parts.joined(separator: " · ")
    }

    private func perform(_ action: () throws -> Void) {
        do {
            guard sourceIdentity == session.identity else { throw APIError.sessionChanged }
            try action(); errorMessage = nil
        } catch { errorMessage = error.localizedDescription }
    }
}
