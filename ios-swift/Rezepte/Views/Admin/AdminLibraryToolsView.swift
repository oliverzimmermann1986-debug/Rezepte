import SwiftUI

struct AdminLibraryToolsView: View {
    private enum Tool: String, CaseIterable, Identifiable {
        case trash = "Papierkorb"
        case versions = "Versionen"
        case audit = "Prüfung"
        var id: String { rawValue }
    }

    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var tool = Tool.trash
    @State private var trash: [TrashRecipe] = []
    @State private var versions: [RecipeVersion] = []
    @State private var audit: AuditFindingsResponse?
    @State private var isLoading = true
    @State private var isWorking = false
    @State private var errorMessage: String?
    @State private var showEmptyConfirmation = false

    var body: some View {
        VStack(spacing: 0) {
            Picker("Werkzeug", selection: $tool) {
                ForEach(Tool.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .padding()

            if isLoading {
                ProgressView("Daten werden geladen …").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let errorMessage, trash.isEmpty, versions.isEmpty, audit == nil {
                ErrorState(message: errorMessage) { Task { await load() } }
            } else {
                switch tool {
                case .trash: trashList
                case .versions: versionList
                case .audit: auditList
                }
            }
        }
        .background(theme.background)
        .navigationTitle("Bibliothek pflegen")
        .toolbar {
            if tool == .trash, !trash.isEmpty {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Leeren", role: .destructive) { showEmptyConfirmation = true }
                }
            }
        }
        .confirmationDialog("Papierkorb endgültig leeren?", isPresented: $showEmptyConfirmation) {
            Button("Alle endgültig löschen", role: .destructive) { Task { await emptyTrash() } }
            Button("Abbrechen", role: .cancel) {}
        } message: {
            Text("Rezepte und zugehörige Dateien können danach nicht wiederhergestellt werden.")
        }
        .task { await load() }
        .task(id: audit?.status.running) {
            while audit?.status.running == true, !Task.isCancelled {
                try? await Task.sleep(for: .seconds(3))
                guard !Task.isCancelled else { return }
                await load()
            }
        }
        .refreshable { await load() }
    }

    private var trashList: some View {
        List {
            if trash.isEmpty {
                EmptyState(icon: "trash", title: "Papierkorb leer", message: "Gelöschte Rezepte bleiben hier bis zu 30 Tage wiederherstellbar.")
            } else {
                ForEach(trash) { recipe in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(recipe.name).font(.headline)
                        Text("Noch etwa \(Int((recipe.daysUntilPurge ?? 0).rounded(.up))) Tage wiederherstellbar")
                            .font(.caption).foregroundStyle(.secondary)
                        HStack {
                            Button("Wiederherstellen", systemImage: "arrow.uturn.backward") {
                                Task { await restore(recipe) }
                            }.buttonStyle(.borderedProminent)
                            Button("Endgültig löschen", systemImage: "trash", role: .destructive) {
                                Task { await purge(recipe) }
                            }.buttonStyle(.bordered)
                        }
                    }
                    .padding(.vertical, 4)
                    .disabled(isWorking)
                }
            }
        }
        .scrollContentBackground(.hidden)
    }

    private var versionList: some View {
        List {
            if versions.isEmpty {
                EmptyState(icon: "clock.arrow.circlepath", title: "Noch keine Versionen", message: "Vor Änderungen legt der Server automatisch einen wiederherstellbaren Stand an.")
            } else {
                ForEach(versions) { version in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(version.recipeName ?? "Rezept #\(version.recipeId)").font(.headline)
                        Text(version.reason ?? "Änderung").font(.subheadline)
                        Text(Date(timeIntervalSince1970: version.createdAt), style: .date)
                            .font(.caption).foregroundStyle(.secondary)
                        Button("Diesen Stand wiederherstellen", systemImage: "clock.arrow.circlepath") {
                            Task { await restore(version) }
                        }.buttonStyle(.bordered)
                    }
                    .padding(.vertical, 4)
                    .disabled(isWorking)
                }
            }
        }
        .scrollContentBackground(.hidden)
    }

    private var auditList: some View {
        List {
            Section {
                Button {
                    Task { await startAudit() }
                } label: {
                    Label(audit?.status.running == true ? "KI-Prüfung läuft …" : "Bibliothek mit KI prüfen", systemImage: "checkmark.magnifyingglass")
                }
                .disabled(isWorking || audit?.status.running == true)
                if let status = audit?.status, status.running {
                    ProgressView(value: Double(status.processed), total: Double(max(1, status.total)))
                    Text("\(status.processed) von \(status.total) geprüft · \(status.findings) Hinweise")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            if let findings = audit?.items, findings.isEmpty {
                Section { EmptyState(icon: "checkmark.seal", title: "Keine offenen KI-Hinweise", message: "Name, Kategorie und Ablage sind konsistent.") }
            } else {
                ForEach(audit?.items ?? []) { finding in
                    VStack(alignment: .leading, spacing: 7) {
                        Text(finding.recipeName).font(.headline)
                        Text(finding.reason ?? finding.findingType).font(.caption).foregroundStyle(.secondary)
                        if let current = finding.currentValue, let suggested = finding.suggestedValue {
                            Text("\(current) → \(suggested)").font(.subheadline.bold())
                        }
                        HStack {
                            Button("Übernehmen", systemImage: "checkmark") { Task { await apply(finding) } }
                                .buttonStyle(.borderedProminent)
                            Button("Ignorieren") { Task { await resolve(finding) } }
                                .buttonStyle(.bordered)
                        }
                    }
                    .padding(.vertical, 4)
                    .disabled(isWorking)
                }
            }
        }
        .scrollContentBackground(.hidden)
    }

    private func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            async let trashResponse = session.api.trash()
            async let versionsResponse = session.api.recipeVersions()
            async let auditResponse = session.api.auditFindings()
            let loadedTrash = try await trashResponse
            let loadedVersions = try await versionsResponse
            let loadedAudit = try await auditResponse
            trash = loadedTrash.items
            versions = loadedVersions.items
            audit = loadedAudit
        } catch { errorMessage = error.localizedDescription; session.handle(error) }
    }

    private func restore(_ recipe: TrashRecipe) async { await perform { _ = try await session.api.restoreTrashRecipe(id: recipe.id) } }
    private func purge(_ recipe: TrashRecipe) async { await perform { _ = try await session.api.purgeTrashRecipe(id: recipe.id) } }
    private func restore(_ version: RecipeVersion) async { await perform { _ = try await session.api.restoreRecipeVersion(id: version.id) } }
    private func apply(_ finding: AuditFinding) async { await perform { _ = try await session.api.applyAuditFinding(id: finding.id) } }
    private func resolve(_ finding: AuditFinding) async { await perform { _ = try await session.api.resolveAuditFinding(id: finding.id) } }
    private func emptyTrash() async { await perform { _ = try await session.api.emptyTrash() } }

    private func startAudit() async {
        await perform(reloadImmediately: true) { _ = try await session.api.startAudit() }
    }

    private func perform(reloadImmediately: Bool = true, action: () async throws -> Void) async {
        isWorking = true
        defer { isWorking = false }
        do {
            try await action()
            if reloadImmediately { await load() }
            NotificationCenter.default.post(name: .recipesChanged, object: nil)
        } catch { errorMessage = error.localizedDescription; session.handle(error) }
    }
}
