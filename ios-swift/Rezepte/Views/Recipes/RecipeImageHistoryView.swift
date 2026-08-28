import SwiftUI
import UIKit

struct RecipeImageHistoryView: View {
    let recipeID: Int
    let recipeName: String

    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var recipe: Recipe?
    @State private var backups: [ImageBackup] = []
    @State private var isLoading = true
    @State private var isGenerating = false
    @State private var restoringBackup: ImageBackup?
    @State private var errorMessage: String?
    @State private var refreshToken = UUID()

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 8) {
                    Label("Jede Änderung bleibt rückholbar", systemImage: "shield.lefthalf.filled.badge.checkmark")
                        .font(.headline)
                    Text("Vor jedem Austausch wird das vorhandene Bild checksummiert gesichert. Hier kannst du Original und generierte Fassung vergleichen.")
                        .font(.subheadline)
                        .foregroundStyle(theme.muted)
                }
                .cardSurface()

                currentImageCard

                Button {
                    Task { await generateImage() }
                } label: {
                    HStack {
                        if isGenerating { ProgressView() }
                        Label(
                            isGenerating ? "Bild wird erzeugt …" : "Neues Rezeptbild generieren",
                            systemImage: "sparkles"
                        )
                    }
                    .frame(maxWidth: .infinity, minHeight: 46)
                }
                .buttonStyle(.borderedProminent)
                .disabled(isGenerating)

                if backups.isEmpty, !isLoading {
                    ContentUnavailableView(
                        "Noch keine Sicherung",
                        systemImage: "externaldrive",
                        description: Text("Beim ersten Bildaustausch wird das bisherige Bild hier abgelegt.")
                    )
                } else {
                    Text("Gesicherte Originale")
                        .font(.title2.bold())

                    ForEach(backups) { backup in
                        backupCard(backup)
                    }
                }

                if let errorMessage {
                    Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote)
                        .foregroundStyle(theme.danger)
                        .cardSurface()
                }
            }
            .padding()
        }
        .background(theme.background)
        .navigationTitle("Bildverlauf")
        .navigationBarTitleDisplayMode(.inline)
        .overlay { if isLoading { ProgressView() } }
        .task { await load() }
        .confirmationDialog(
            "Originalbild wiederherstellen?",
            isPresented: Binding(
                get: { restoringBackup != nil },
                set: { if !$0 { restoringBackup = nil } }
            ),
            titleVisibility: .visible
        ) {
            Button("Diese Sicherung wiederherstellen") {
                guard let backup = restoringBackup else { return }
                Task { await restore(backup) }
            }
            Button("Abbrechen", role: .cancel) { restoringBackup = nil }
        } message: {
            Text("Das aktuelle Bild wird ersetzt. Die Sicherung selbst bleibt im Verlauf erhalten.")
        }
    }

    private var currentImageCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Aktuelle Fassung").font(.headline)
                    Text(recipeName).font(.caption).foregroundStyle(theme.muted)
                }
                Spacer()
                if let status = recipe?.imageGenerationStatus {
                    statusBadge(status)
                }
            }
            AuthenticatedImage(recipeID: recipeID, height: 240)
                .id(refreshToken)
                .clipShape(RoundedRectangle(cornerRadius: 18))
            if let model = recipe?.imageGenerationModel?.nilIfEmpty {
                Label(model, systemImage: "cpu")
                    .font(.caption)
                    .foregroundStyle(theme.muted)
            }
        }
        .cardSurface()
    }

    private func backupCard(_ backup: ImageBackup) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(backup.originalFilename).font(.headline)
                    Text(backupDate(backup.createdAt))
                        .font(.caption)
                        .foregroundStyle(theme.muted)
                }
                Spacer()
                if backup.restoredAt != nil {
                    Label("Wiederhergestellt", systemImage: "arrow.uturn.backward.circle.fill")
                        .font(.caption.bold())
                        .foregroundStyle(theme.success)
                }
            }

            BackupImage(backupID: backup.id, height: 210)
                .clipShape(RoundedRectangle(cornerRadius: 16))

            HStack {
                Label(String(backup.originalSha256.prefix(10)), systemImage: "number")
                    .font(.caption.monospaced())
                    .foregroundStyle(theme.muted)
                Spacer()
                Button("Original einsetzen", systemImage: "arrow.uturn.backward") {
                    restoringBackup = backup
                }
                .buttonStyle(.bordered)
            }
        }
        .cardSurface()
    }

    private func statusBadge(_ status: String) -> some View {
        let presentation: (String, String, Color) = switch status {
        case "ok": ("Generiert", "sparkles", theme.success)
        case "running", "pending": ("In Arbeit", "hourglass", theme.warning)
        case "error": ("Fehler", "exclamationmark.triangle.fill", theme.danger)
        case "restored": ("Original", "arrow.uturn.backward", theme.accent)
        default: ("Gesichert", "externaldrive", theme.muted)
        }
        return Label(presentation.0, systemImage: presentation.1)
            .font(.caption.bold())
            .foregroundStyle(presentation.2)
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            async let recipeCall = session.api.recipe(id: recipeID)
            async let backupCall = session.api.imageBackups(recipeID: recipeID)
            let (loadedRecipe, loadedBackups) = try await (recipeCall, backupCall)
            recipe = loadedRecipe
            backups = loadedBackups.items
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func generateImage() async {
        guard !isGenerating else { return }
        isGenerating = true
        errorMessage = nil
        defer { isGenerating = false }
        do {
            _ = try await session.api.generateRecipeImage(id: recipeID)
            for _ in 0..<60 where !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
                let updated = try await session.api.recipe(id: recipeID)
                recipe = updated
                if !["pending", "running", "backed_up"].contains(updated.imageGenerationStatus ?? "") {
                    await load()
                    refreshToken = UUID()
                    NotificationCenter.default.post(name: .recipesChanged, object: recipeID)
                    return
                }
            }
            errorMessage = "Die Generierung läuft weiter. Ziehe zum Aktualisieren erneut in diese Ansicht."
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func restore(_ backup: ImageBackup) async {
        restoringBackup = nil
        do {
            _ = try await session.api.restoreImageBackup(id: backup.id)
            await load()
            refreshToken = UUID()
            NotificationCenter.default.post(name: .recipesChanged, object: recipeID)
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func backupDate(_ timestamp: Double) -> String {
        Date(timeIntervalSince1970: timestamp).formatted(date: .abbreviated, time: .shortened)
    }
}

private struct BackupImage: View {
    let backupID: Int
    let height: CGFloat

    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var image: UIImage?
    @State private var failed = false

    var body: some View {
        ZStack {
            theme.accentSoft
            if let image {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                Image(systemName: failed ? "exclamationmark.triangle" : "photo")
                    .font(.system(size: 28, weight: .medium))
                    .foregroundStyle(theme.muted)
            }
        }
        .frame(maxWidth: .infinity)
        .frame(height: height)
        .clipped()
        .task(id: backupID) { await load() }
    }

    private func load() async {
        do {
            let request = try await session.api.imageBackupRequest(backupID: backupID)
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse,
                  (200..<300).contains(http.statusCode),
                  let loaded = UIImage(data: data) else {
                failed = true
                return
            }
            image = loaded
        } catch {
            failed = true
        }
    }
}
