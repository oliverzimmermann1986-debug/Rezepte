import SwiftUI

struct ShoppingToolsView: View {
    let onApplied: () async -> Void

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme
    @State private var preview: ShoppingOptimizePreview?
    @State private var exportText: String?
    @State private var isWorking = false
    @State private var notice: String?
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            List {
                Section("KI-Sortierung") {
                    Text("Die KI vereinheitlicht Namen, führt doppelte Artikel zusammen und ordnet sie Supermarkt-Kategorien zu. Vor dem Übernehmen siehst du eine Vorschau.")
                        .font(.caption).foregroundStyle(theme.muted)
                    Button("Vorschau erstellen", systemImage: "sparkles") {
                        Task { await createPreview() }
                    }.disabled(isWorking)
                }

                if let preview {
                    Section("Vorschau · \(preview.items.count) Artikel") {
                        ForEach(preview.items) { item in
                            HStack {
                                Text(item.icon ?? "🛒")
                                VStack(alignment: .leading) {
                                    Text(item.name)
                                    Text([amount(item.amount), item.unit, item.category].compactMap { $0 }.joined(separator: " · "))
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                        Button("Vorschau übernehmen", systemImage: "checkmark.circle.fill") {
                            Task { await apply(preview) }
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(isWorking)
                    }
                }

                Section("Teilen & übertragen") {
                    Button("Textliste vorbereiten", systemImage: "doc.on.doc") {
                        Task { await prepareExport() }
                    }.disabled(isWorking)
                    if let exportText {
                        ShareLink(item: exportText, subject: Text("Einkaufsliste")) {
                            Label("Einkaufsliste teilen", systemImage: "square.and.arrow.up")
                        }
                        Text(exportText).font(.caption).textSelection(.enabled)
                    }
                    if session.supports("einkauf-proxy") {
                        Button("An Einkauf-App senden", systemImage: "paperplane") {
                            Task { await push() }
                        }.disabled(isWorking)
                    }
                }

                if let notice { Section { Label(notice, systemImage: "checkmark.circle.fill").foregroundStyle(theme.success) } }
                if let errorMessage { Section { Text(errorMessage).foregroundStyle(.red) } }
            }
            .navigationTitle("Einkauf optimieren")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Schließen") { dismiss() } }
            }
        }
    }

    private func createPreview() async {
        await perform { preview = try await session.api.shoppingOptimizationPreview() }
    }

    private func apply(_ preview: ShoppingOptimizePreview) async {
        await perform {
            _ = try await session.api.applyShoppingOptimization(previewID: preview.previewId)
            notice = "Optimierte Liste wurde übernommen."
            self.preview = nil
            await onApplied()
        }
    }

    private func prepareExport() async {
        await perform { exportText = try await session.api.shoppingExportText() }
    }

    private func push() async {
        await perform {
            let result = try await session.api.pushShoppingToEinkauf()
            if result.ok {
                notice = "\(result.pushed ?? 0) Artikel wurden übertragen."
            } else {
                throw ShoppingToolError.pushFailed(result.error ?? "Übertragung fehlgeschlagen")
            }
        }
    }

    private func perform(_ action: () async throws -> Void) async {
        isWorking = true
        errorMessage = nil
        defer { isWorking = false }
        do { try await action() }
        catch { errorMessage = error.localizedDescription; session.handle(error) }
    }

    private func amount(_ value: Double?) -> String? {
        guard let value else { return nil }
        return value.rounded() == value ? String(Int(value)) : String(format: "%.2f", value)
    }
}

private enum ShoppingToolError: LocalizedError {
    case pushFailed(String)
    var errorDescription: String? {
        switch self { case let .pushFailed(message): message }
    }
}
