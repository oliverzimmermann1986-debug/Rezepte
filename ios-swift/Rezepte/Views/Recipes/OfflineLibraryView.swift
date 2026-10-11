import SwiftUI

/// A direct entry point stays usable when online browsing cannot load at all.
@MainActor
struct OfflineLibraryView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var offline: OfflineRecipeStore
    @Environment(\.dismiss) private var dismiss
    @State private var error: String?

    var body: some View {
        NavigationStack {
          List {
            Section {
                Text("Deine Downloads enthalten Rezepttexte, Zutaten, Schritte und verfügbare Bilder. Sie bleiben für dieses Konto und diesen Haushalt auf diesem Gerät gespeichert.")
                    .font(.callout).foregroundStyle(.secondary)
                if let progress = offline.progress { ProgressView(progress) }
                if let error = error ?? offline.errorMessage { Text(error).foregroundStyle(.red) }
            }
            if !offline.cachedCookbooks(session: session).isEmpty {
                Section("Heruntergeladene Kochbücher") {
                    ForEach(offline.cachedCookbooks(session: session)) { book in
                        NavigationLink {
                            List(offline.cachedCookbookRecipes(id: book.id, session: session)) { recipe in
                                NavigationLink(recipe.name) { RecipeDetailView(recipeID: recipe.id) }
                            }.navigationTitle(book.name)
                        } label: {
                            Label("\(book.name) · \(book.recipeCount) Rezepte", systemImage: "books.vertical")
                        }
                        .swipeActions {
                            Button("Download entfernen", role: .destructive) {
                                do { try offline.removeCookbook(id: book.id, session: session) }
                                catch { self.error = error.localizedDescription }
                            }.disabled(offline.isDownloading)
                        }
                    }
                }
            }
            Section("Alle Offline-Rezepte") {
                if offline.cachedRecipes(session: session).isEmpty {
                    ContentUnavailableView("Noch keine Downloads", systemImage: "arrow.down.circle",
                        description: Text("Öffne ein Rezept oder Kochbuch und wähle „Offline speichern“ aus."))
                }
                ForEach(offline.cachedRecipes(session: session)) { record in
                    NavigationLink { RecipeDetailView(recipeID: record.id) } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(record.recipe.name)
                            Text("Gespeichert \(record.savedAt.formatted(date: .abbreviated, time: .shortened))")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
          }
          .navigationTitle("Offline-Rezepte")
          .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Fertig") { dismiss() } } }
          .task(id: session.identity) { await offline.activate(session: session) }
        }
    }
}
