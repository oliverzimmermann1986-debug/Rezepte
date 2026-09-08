import SwiftUI

struct OfflineRecipesView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var records: [OfflineRecipe] = []
    @State private var search = ""
    @State private var errorMessage: String?

    private var filtered: [OfflineRecipe] {
        records.filter { search.isEmpty || $0.recipe.name.localizedCaseInsensitiveContains(search) }
    }

    var body: some View {
        List {
            Section {
                Text("Zutaten und Schritte geöffneter Rezepte bleiben auf diesem iPhone. Bilder und Originalseiten benötigen gegebenenfalls Internet. Beim Abmelden werden diese lokalen Daten gelöscht.")
                    .font(.callout).foregroundStyle(theme.muted)
                if session.isOffline {
                    Label("Offline · nur lokale Daten", systemImage: "wifi.slash")
                        .foregroundStyle(theme.warning)
                }
                if session.pendingCookingCount > 0 {
                    Text("\(session.pendingCookingCount) Kochabschlüsse warten auf Übertragung.")
                        .font(.callout)
                }
                Button("Verbindung prüfen und abgleichen", systemImage: "arrow.triangle.2.circlepath") {
                    Task { await session.refreshAccess(); load() }
                }
            }
            if let errorMessage {
                Text(errorMessage).foregroundStyle(theme.danger)
            }
            Section("Auf diesem iPhone · \(records.count)") {
                if records.isEmpty {
                    Text("Öffne ein Rezept mit Internetverbindung. Zutaten und Schritte werden dann automatisch gespeichert.")
                        .foregroundStyle(theme.muted)
                }
                ForEach(filtered) { record in
                    NavigationLink {
                        RecipeDetailView(recipeID: record.id, offlineRecipe: record.recipe)
                    } label: {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(record.recipe.name).font(.headline)
                            Text("Stand \(record.savedAt.formatted(date: .abbreviated, time: .shortened))")
                                .font(.caption).foregroundStyle(theme.muted)
                            if hasProgress(record.id) {
                                Label("Kochen fortsetzen", systemImage: "play.circle")
                                    .font(.caption.bold()).foregroundStyle(theme.accent)
                            }
                        }
                    }
                    .accessibilityIdentifier("offlineRecipe-\(record.id)")
                }
            }
        }
        .navigationTitle("Offline-Regal")
        .searchable(text: $search, prompt: "Lokale Rezepte durchsuchen")
        .task { load() }
    }

    private func hasProgress(_ id: Int) -> Bool {
        guard let account = session.offlineAccount else { return false }
        return (try? session.offlineStore.progress(account: account).contains { $0.recipeID == id && $0.started }) ?? false
    }

    private func load() {
        guard let account = session.offlineAccount else { records = []; return }
        do {
            records = try session.offlineStore.recipes(account: account)
            errorMessage = nil
            session.refreshPendingCookingCount()
        } catch { errorMessage = error.localizedDescription }
    }
}
