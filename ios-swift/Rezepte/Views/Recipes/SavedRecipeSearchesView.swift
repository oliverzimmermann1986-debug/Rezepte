import SwiftUI

struct SavedRecipeSearchesView: View {
    @EnvironmentObject private var searches: SavedRecipeSearchStore
    @Environment(\.dismiss) private var dismiss
    let query: String
    let filters: RecipeFilters
    let onApply: (SavedRecipeSearch) -> Void
    @State private var name = ""
    @State private var renaming: SavedRecipeSearch?
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            List {
                Section("Aktuelle Suche speichern") {
                    TextField("Name, z. B. Vegetarische Favoriten", text: $name)
                    Text(query.isEmpty ? "Alle Suchbegriffe · \(filters.activeCount) Filter" : "\(query) · \(filters.activeCount) Filter")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("Suche speichern", systemImage: "bookmark") {
                        perform { try searches.save(name: name, query: query, filters: filters); name = "" }
                    }
                    .disabled(!searches.isReady || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                Section {
                    if searches.items.isEmpty {
                        Text("Noch keine gespeicherten Suchen.").foregroundStyle(.secondary)
                    }
                    ForEach(searches.items) { item in
                        Button {
                            onApply(item)
                            dismiss()
                        } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(item.name).font(.headline)
                                Text("\(item.query.isEmpty ? "Alle Suchbegriffe" : item.query) · \(item.filters.activeCount) Filter · \(item.filters.library.title)")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .swipeActions {
                            Button("Löschen", role: .destructive) { perform { try searches.remove(id: item.id) } }
                            Button("Umbenennen") { renaming = item; name = item.name }.tint(.blue)
                        }
                        .contextMenu {
                            Button("Umbenennen") { renaming = item; name = item.name }
                            Button("Löschen", role: .destructive) { perform { try searches.remove(id: item.id) } }
                        }
                    }
                } header: { Text("Gespeicherte Suchen") } footer: {
                    Text("Suchbegriffe, Sammlung und alle Filter werden auf diesem Gerät für dein Konto und deinen Haushalt gespeichert.")
                }
                if let message = errorMessage ?? searches.errorMessage {
                    Section { Text(message).foregroundStyle(.red) }
                }
            }
            .navigationTitle("Gespeicherte Suchen")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Schließen") { dismiss() } } }
            .alert("Suche umbenennen", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
                TextField("Name", text: $name)
                Button("Speichern") {
                    if let item = renaming { perform { try searches.rename(id: item.id, name: name) } }
                    renaming = nil; name = ""
                }
                Button("Abbrechen", role: .cancel) { renaming = nil; name = "" }
            }
        }
    }

    private func perform(_ action: () throws -> Void) {
        do { try action(); errorMessage = nil } catch { errorMessage = error.localizedDescription }
    }
}
