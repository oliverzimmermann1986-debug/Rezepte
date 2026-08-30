import PDFKit
import SwiftUI

struct RecipeMetadataEditorView: View {
    let recipe: Recipe
    let onSaved: () async -> Void

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var type: String
    @State private var category: String
    @State private var description: String
    @State private var servings: Int
    @State private var sourceURL: String
    @State private var tags: String
    @State private var isSaving = false
    @State private var errorMessage: String?

    init(recipe: Recipe, onSaved: @escaping () async -> Void) {
        self.recipe = recipe
        self.onSaved = onSaved
        _name = State(initialValue: recipe.name)
        _type = State(initialValue: recipe.type ?? "Sonstiges")
        _category = State(initialValue: recipe.category ?? "Allgemein")
        _description = State(initialValue: recipe.description ?? "")
        _servings = State(initialValue: recipe.servings ?? 2)
        _sourceURL = State(initialValue: recipe.url ?? "")
        _tags = State(initialValue: (recipe.tags ?? []).map(\.name).joined(separator: ", "))
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Rezept") {
                    TextField("Name", text: $name)
                    TextField("Typ", text: $type)
                    TextField("Kategorie", text: $category)
                    Stepper("\(servings) Portionen", value: $servings, in: 1...50)
                }
                Section("Beschreibung") {
                    TextEditor(text: $description).frame(minHeight: 140)
                }
                Section("Quelle und Tags") {
                    TextField("https://…", text: $sourceURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("Tags, durch Komma getrennt", text: $tags)
                }
                if let errorMessage {
                    Section { Text(errorMessage).foregroundStyle(.red) }
                }
            }
            .navigationTitle("Rezept bearbeiten")
            .navigationBarTitleDisplayMode(.inline)
            .interactiveDismissDisabled(isSaving)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Abbrechen") { dismiss() }.disabled(isSaving)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Speichern") { Task { await save() } }
                        .disabled(isSaving || name.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
    }

    private func save() async {
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }
        do {
            _ = try await session.api.updateRecipeMetadata(
                id: recipe.id,
                name: name.trimmingCharacters(in: .whitespacesAndNewlines),
                type: type.trimmingCharacters(in: .whitespacesAndNewlines),
                category: category.trimmingCharacters(in: .whitespacesAndNewlines),
                description: description,
                servings: servings,
                url: sourceURL.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
            )
            let normalizedTags = tags.split(separator: ",")
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
            _ = try await session.api.updateRecipeTags(id: recipe.id, tags: normalizedTags)
            await onSaved()
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }
}

struct RecipeShareLinksView: View {
    let recipeID: Int
    let recipeName: String

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @State private var expiryDays = 7
    @State private var links: [RecipeShareLink] = []
    @State private var createdURL: URL?
    @State private var isWorking = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            List {
                Section("Neue Freigabe") {
                    Picker("Gültigkeit", selection: $expiryDays) {
                        Text("1 Tag").tag(1)
                        Text("7 Tage").tag(7)
                        Text("30 Tage").tag(30)
                    }
                    Button("Sicheren Link erstellen", systemImage: "link.badge.plus") {
                        Task { await create() }
                    }
                    .disabled(isWorking)
                    if let createdURL {
                        ShareLink(item: createdURL, subject: Text(recipeName)) {
                            Label("Neuen Link teilen", systemImage: "square.and.arrow.up")
                        }
                        Text(createdURL.absoluteString).font(.caption).textSelection(.enabled)
                    }
                }
                Section("Aktive und frühere Links") {
                    if links.isEmpty {
                        Text("Noch keine Freigaben").foregroundStyle(.secondary)
                    }
                    ForEach(links) { link in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(link.active == true ? "Aktiv" : "Abgelaufen oder widerrufen")
                                Text(Date(timeIntervalSince1970: link.expiresAt), style: .date)
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if link.active == true {
                                Button("Widerrufen", role: .destructive) {
                                    Task { await revoke(link) }
                                }
                            }
                        }
                    }
                }
                if let errorMessage { Section { Text(errorMessage).foregroundStyle(.red) } }
            }
            .navigationTitle("Rezept freigeben")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Fertig") { dismiss() } }
            }
            .task { await load() }
        }
    }

    private func load() async {
        do { links = try await session.api.recipeShares(id: recipeID).items }
        catch { errorMessage = error.localizedDescription; session.handle(error) }
    }

    private func create() async {
        isWorking = true
        defer { isWorking = false }
        do {
            let result = try await session.api.createRecipeShare(id: recipeID, expiresDays: expiryDays)
            createdURL = URL(string: result.url)
            await load()
        } catch { errorMessage = error.localizedDescription; session.handle(error) }
    }

    private func revoke(_ link: RecipeShareLink) async {
        do {
            _ = try await session.api.revokeRecipeShare(recipeID: recipeID, shareID: link.id)
            await load()
        } catch { errorMessage = error.localizedDescription; session.handle(error) }
    }
}

struct PDFPreviewSheet: View {
    let title: String
    let loader: () async throws -> Data

    @Environment(\.dismiss) private var dismiss
    @State private var data: Data?
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Group {
                if let data { PDFKitView(data: data) }
                else if let errorMessage { ErrorState(message: errorMessage) { Task { await load() } } }
                else { ProgressView("PDF wird geladen …") }
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Schließen") { dismiss() } }
            }
            .task { await load() }
        }
    }

    private func load() async {
        errorMessage = nil
        do { data = try await loader() }
        catch { errorMessage = error.localizedDescription }
    }
}

private struct PDFKitView: UIViewRepresentable {
    let data: Data

    func makeUIView(context: Context) -> PDFView {
        let view = PDFView()
        view.autoScales = true
        view.displayMode = .singlePageContinuous
        view.displayDirection = .vertical
        return view
    }

    func updateUIView(_ view: PDFView, context: Context) {
        if view.document == nil { view.document = PDFDocument(data: data) }
    }
}
