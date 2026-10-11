import SwiftUI

@MainActor
struct RecipeTrashView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @State private var recipes: [TrashRecipe] = []
    @State private var loadedIdentity: UUID?
    @State private var loadID = UUID()
    @State private var restoreID = UUID()
    @State private var isLoading = false
    @State private var restoringRecipeID: Int?
    @State private var errorMessage: String?
    @State private var notice: String?
    @State private var hasMore = false

    private var allowed: Bool { !session.readOnly && session.role != .guest && !session.isOfflineSession }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("Gelöschte Rezepte bleiben 30 Tage im Papierkorb. Danach werden sie automatisch endgültig entfernt.")
                    Text("Nach dem Wiederherstellen kannst du das Rezept bei Bedarf erneut offline speichern.")
                        .foregroundStyle(.secondary)
                }
                if !allowed {
                    ContentUnavailableView("Papierkorb nicht verfügbar", systemImage: "wifi.slash",
                                           description: Text("Zum Wiederherstellen bitte mit deinem Konto online anmelden."))
                } else if loadedIdentity != session.identity || isLoading {
                    ProgressView("Papierkorb wird geladen …")
                } else {
                    if let errorMessage {
                        Section {
                            Text(errorMessage).foregroundStyle(.red)
                            Button("Erneut laden") { Task { await load() } }
                                .disabled(restoringRecipeID != nil)
                        }
                    }
                    if let notice { Text(notice).foregroundStyle(.secondary).accessibilityAddTraits(.updatesFrequently) }
                    if recipes.isEmpty && errorMessage == nil {
                        ContentUnavailableView("Dein Papierkorb ist leer", systemImage: "trash",
                                               description: Text("Hier findest du gelöschte Rezepte, die du wiederherstellen darfst."))
                    }
                    ForEach(recipes) { recipe in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(recipe.name).font(.headline)
                            if let days = recipe.daysUntilPurge, days.isFinite {
                                Text(days <= 0 ? "Wird demnächst endgültig entfernt" : "Noch etwa \(Int(ceil(min(days, 30)))) Tage im Papierkorb")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Button {
                                Task { await restore(recipe) }
                            } label: {
                                HStack {
                                    Label("Wiederherstellen", systemImage: "arrow.uturn.backward")
                                    if restoringRecipeID == recipe.id { ProgressView() }
                                }
                                .frame(minHeight: 44)
                            }
                            .disabled(restoringRecipeID != nil)
                            .accessibilityLabel("\(recipe.name) wiederherstellen")
                        }
                    }
                    if hasMore { Text("Es werden die zuletzt gelöschten Rezepte angezeigt.").font(.caption).foregroundStyle(.secondary) }
                }
            }
            .navigationTitle("Papierkorb")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Schließen") { dismiss() }.disabled(restoringRecipeID != nil)
                }
            }
            .interactiveDismissDisabled(restoringRecipeID != nil)
            .refreshable { await load() }
            .task(id: session.identity) {
                loadID = UUID(); restoreID = UUID(); recipes = []; restoringRecipeID = nil
                loadedIdentity = session.identity; errorMessage = nil; notice = nil; hasMore = false
                await load()
            }
            .onDisappear { loadID = UUID(); restoreID = UUID() }
        }
    }

    private func load() async {
        guard allowed, restoringRecipeID == nil, !Task.isCancelled else { return }
        let identity = session.identity, request = UUID()
        loadID = request; isLoading = true; errorMessage = nil
        defer { if loadID == request, session.identity == identity { isLoading = false } }
        do {
            let response = try await session.api.trash()
            guard loadID == request, session.identity == identity, !Task.isCancelled else { return }
            recipes = response.items.filter { $0.canRestore(isAdmin: session.role == .admin) }
            hasMore = response.total > response.items.count
            loadedIdentity = identity
        } catch {
            guard loadID == request, session.identity == identity, !Task.isCancelled else { return }
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func restore(_ recipe: TrashRecipe) async {
        guard allowed, loadedIdentity == session.identity, restoringRecipeID == nil,
              recipe.canRestore(isAdmin: session.role == .admin), !Task.isCancelled else { return }
        let identity = session.identity, request = UUID()
        restoreID = request; restoringRecipeID = recipe.id; errorMessage = nil; notice = nil
        // An older list request must not reinsert a just-restored recipe.
        loadID = UUID(); isLoading = false
        defer { if restoreID == request, session.identity == identity { restoringRecipeID = nil } }
        do {
            let result = try await session.api.restoreTrashRecipe(id: recipe.id)
            guard restoreID == request, session.identity == identity, !Task.isCancelled else { return }
            guard result.ok == true else {
                errorMessage = result.message ?? "Das Wiederherstellen wurde nicht bestätigt. Bitte erneut versuchen."
                return
            }
            recipes.removeAll { $0.id == recipe.id }
            notice = "„\(recipe.name)“ wurde wiederhergestellt."
            NotificationCenter.default.post(name: .recipesChanged, object: recipe.id)
        } catch {
            guard restoreID == request, session.identity == identity, !Task.isCancelled else { return }
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }
}
