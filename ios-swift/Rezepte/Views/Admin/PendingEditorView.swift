import SwiftUI

struct PendingEditorView: View {
    let item: PendingItem
    let onChanged: () async -> Void

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var recipeType: String
    @State private var category: String
    @State private var isSaving = false
    @State private var errorMessage: String?

    init(item: PendingItem, onChanged: @escaping () async -> Void) {
        self.item = item
        self.onChanged = onChanged
        _name = State(initialValue: item.aiSuggestion?.name ?? "")
        _recipeType = State(initialValue: item.aiSuggestion?.type ?? "Hauptgericht")
        _category = State(initialValue: item.aiSuggestion?.category ?? "Allgemein")
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Import") {
                    TextField("Rezeptname", text: $name)
                    TextField("Typ", text: $recipeType)
                    TextField("Kategorie", text: $category)
                }

                if let description = item.description?.nilIfEmpty {
                    Section("Erkannter Text") {
                        Text(description)
                            .font(.callout)
                            .textSelection(.enabled)
                    }
                }

                Section("Quelle") {
                    Text(item.url)
                        .font(.caption)
                        .textSelection(.enabled)
                    if let filename = item.aiSuggestion?.filename?.nilIfEmpty {
                        LabeledContent("Datei", value: filename)
                    }
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage)
                            .foregroundStyle(AppTheme.warning)
                    }
                }

                Section {
                    Button(role: .destructive) {
                        Task { await resolve(action: "skip") }
                    } label: {
                        Label("Import verwerfen", systemImage: "trash")
                    }
                }
            }
            .navigationTitle("Import bearbeiten")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Abbrechen") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isSaving ? "Speichert …" : "Speichern") {
                        Task { await resolve(action: "save") }
                    }
                    .disabled(isSaving || name.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
    }

    private func resolve(action: String) async {
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }
        do {
            let result = try await session.api.resolvePending(
                url: item.url,
                action: action,
                name: name.trimmingCharacters(in: .whitespacesAndNewlines),
                type: recipeType.trimmingCharacters(in: .whitespacesAndNewlines),
                category: category.trimmingCharacters(in: .whitespacesAndNewlines)
            )
            guard result.ok != false else {
                errorMessage = result.message ?? "Der Import konnte nicht gespeichert werden."
                return
            }
            await onChanged()
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
