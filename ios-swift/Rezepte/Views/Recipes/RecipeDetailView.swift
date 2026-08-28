import SwiftUI

struct RecipeDetailView: View {
    let recipeID: Int

    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var themeStore: ThemeStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @Environment(\.recipeTheme) private var theme
    @State private var recipe: Recipe?
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var showIngredientsEditor = false
    @State private var showStepsEditor = false
    @State private var cartConfirmation = false
    @State private var showOriginalText = false
    @State private var showDeleteConfirmation = false
    @State private var isDeleting = false
    @State private var imageRefreshToken = UUID()
    @State private var comments: [RecipeComment] = []
    @State private var commentDraft = ""
    @State private var commentsLoading = false
    @State private var commentPosting = false
    @State private var commentError: String?
    @State private var commentRequestID = UUID()

    var body: some View {
        Group {
            if isLoading && recipe == nil {
                ProgressView("Rezept wird geladen …")
            } else if let errorMessage, recipe == nil {
                ErrorState(message: errorMessage) {
                    Task { await load() }
                }
            } else if let recipe {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 22) {
                        AuthenticatedImage(recipeID: recipe.id, height: 270)
                            .id(imageRefreshToken)
                            .clipShape(RoundedRectangle(cornerRadius: 24))

                        VStack(alignment: .leading, spacing: 8) {
                            Text(recipe.name)
                                .font(.largeTitle.bold())
                                .foregroundStyle(theme.ink)
                        }

                        if recipe.needsManualCare {
                            ManualCareBanner(reasons: recipe.manualCareReasons)
                        }

                        actionBar(recipe)
                        recipePassportSection(recipe)

                        ingredientSection(recipe)
                        stepsSection(recipe)

                        if let sourceURL = safeExternalURL(recipe.url) {
                            VStack(alignment: .leading, spacing: 10) {
                                Text("Quelle")
                                    .font(.title2.bold())
                                Button {
                                    openURL(sourceURL)
                                } label: {
                                    Label("Originalquelle öffnen", systemImage: "arrow.up.right.square")
                                        .frame(maxWidth: .infinity, minHeight: 44)
                                }
                                .buttonStyle(.bordered)
                            }
                        }

                        originalTextSection(recipe)
                        commentsSection(recipe)

                        if !session.readOnly {
                            Button(role: .destructive) {
                                showDeleteConfirmation = true
                            } label: {
                                Label(
                                    isDeleting ? "Rezept wird gelöscht …" : "Rezept löschen",
                                    systemImage: "trash"
                                )
                                .frame(maxWidth: .infinity, minHeight: 44)
                            }
                            .buttonStyle(.bordered)
                            .tint(.red)
                            .disabled(isDeleting)
                        }
                    }
                    .padding()
                }
                .background(theme.background)
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let recipe {
                ToolbarItemGroup(placement: .topBarTrailing) {
                    ShareLink(item: shareText(recipe)) {
                        Image(systemName: "square.and.arrow.up")
                    }
                    if !session.readOnly {
                        Button {
                            Task { await toggleFavorite() }
                        } label: {
                            Image(systemName: recipe.isFavorite ? "heart.fill" : "heart")
                                .foregroundStyle(recipe.isFavorite ? Color.red : Color.primary)
                        }
                        .accessibilityLabel(recipe.isFavorite ? "Aus Favoriten entfernen" : "Als Favorit speichern")
                    }
                }
            }
        }
        .overlay(alignment: .bottom) {
            if cartConfirmation {
                Label("Zur Einkaufsliste hinzugefügt", systemImage: "checkmark.circle.fill")
                    .padding(.horizontal, 16)
                    .padding(.vertical, 12)
                    .background(.thinMaterial, in: Capsule())
                    .padding(.bottom, 12)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .sheet(isPresented: $showIngredientsEditor) {
            if let recipe {
                IngredientEditorView(recipe: recipe) {
                    await load()
                }
            }
        }
        .sheet(isPresented: $showStepsEditor) {
            if let recipe {
                StepEditorView(recipe: recipe) {
                    await load()
                }
            }
        }
        .confirmationDialog(
            "Rezept löschen?",
            isPresented: $showDeleteConfirmation,
            titleVisibility: .visible
        ) {
            Button("In Papierkorb verschieben", role: .destructive) {
                Task { await deleteRecipe() }
            }
            Button("Abbrechen", role: .cancel) {}
        } message: {
            Text("Das Rezept kann 30 Tage lang im Admin-Bereich wiederhergestellt werden.")
        }
        .task {
            await load()
            await loadComments()
        }
        .onChange(of: themeStore.commentLanguage) { _, _ in
            Task { await loadComments() }
        }
        .onReceive(NotificationCenter.default.publisher(for: .recipesChanged)) { notification in
            guard notification.object as? Int == recipeID else { return }
            imageRefreshToken = UUID()
            Task { await load() }
        }
    }

    private func actionBar(_ recipe: Recipe) -> some View {
        VStack(spacing: 12) {
            if session.readOnly {
                Label("Gastzugang · Rezept nur ansehen", systemImage: "eye")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(theme.muted)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .cardSurface()
            } else {
                HStack(spacing: 12) {
                    NavigationLink {
                        CookingModeView(recipe: recipe)
                    } label: {
                        Label("Kochen", systemImage: "fork.knife")
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(theme.accent)
                    .foregroundStyle(theme.ink)
                    .disabled(recipe.steps.isEmpty || recipe.servings == nil)

                    Button {
                        Task { await addToCart() }
                    } label: {
                        Label("Einkaufen", systemImage: "cart.badge.plus")
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.bordered)
                }

                if recipe.steps.isEmpty || recipe.servings == nil {
                    Label(
                        recipe.steps.isEmpty
                            ? "Zum Kochen fehlen Zubereitungsschritte."
                            : "Zum Skalieren fehlt die Portionszahl.",
                        systemImage: "info.circle"
                    )
                    .font(.caption)
                    .foregroundStyle(theme.warning)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }

    private func ingredientSection(_ recipe: Recipe) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Zutaten")
                    .font(.title2.bold())
                Spacer()
                if !session.readOnly {
                    Button("Bearbeiten") { showIngredientsEditor = true }
                }
            }
            if recipe.ingredients.isEmpty {
                Text("Keine Zutaten vorhanden. Bitte manuell ergänzen.")
                    .foregroundStyle(theme.warning)
                    .cardSurface()
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(recipe.ingredients.enumerated()), id: \.offset) { index, ingredient in
                        HStack(alignment: .firstTextBaseline, spacing: 12) {
                            Image(systemName: "circle.fill")
                                .font(.system(size: 6))
                                .foregroundStyle(theme.accent)
                            Text(ingredient.displayText)
                            Spacer(minLength: 0)
                        }
                        .padding(.vertical, 11)
                        if index < recipe.ingredients.count - 1 { Divider() }
                    }
                }
                .cardSurface()
            }
        }
    }

    private func recipePassportSection(_ recipe: Recipe) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Rezeptpass")
                    .font(.title2.bold())
                Spacer()
                Label(
                    recipe.userVerified == true ? "Geprüft" : "Zu prüfen",
                    systemImage: recipe.userVerified == true ? "checkmark.seal.fill" : "questionmark.diamond"
                )
                .font(.caption.bold())
                .foregroundStyle(recipe.userVerified == true ? theme.success : theme.warning)
            }

            if let url = safeExternalURL(recipe.url) {
                LabeledContent("Quelle", value: sourceName(url))
            } else {
                LabeledContent("Quelle", value: "Datei oder eigener Eintrag")
            }

            if let status = recipe.imageGenerationStatus?.nilIfEmpty {
                LabeledContent("Bildstatus", value: imageStatusLabel(status))
            }

            if session.fullAccess {
                Divider()
                NavigationLink {
                    RecipeImageHistoryView(recipeID: recipe.id, recipeName: recipe.name)
                } label: {
                    Label("Originale vergleichen & Bild verwalten", systemImage: "photo.on.rectangle.angled")
                }
            }
        }
        .cardSurface()
    }

    private func stepsSection(_ recipe: Recipe) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Zubereitung")
                    .font(.title2.bold())
                Spacer()
                if !session.readOnly {
                    Button("Bearbeiten") { showStepsEditor = true }
                }
            }
            if recipe.steps.isEmpty {
                Text("Keine Schritte vorhanden. Der Quelllink bleibt zur manuellen Pflege erhalten.")
                    .foregroundStyle(theme.warning)
                    .cardSurface()
            } else {
                ForEach(Array(recipe.steps.enumerated()), id: \.offset) { index, step in
                    StepCard(number: index + 1, step: step)
                }
            }
        }
    }

    @ViewBuilder
    private func originalTextSection(_ recipe: Recipe) -> some View {
        if let description = (recipe.descriptionOriginal ?? recipe.description)?
            .trimmingCharacters(in: .whitespacesAndNewlines),
           !description.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                Button {
                    withAnimation(.snappy) { showOriginalText.toggle() }
                } label: {
                    HStack {
                        Label(
                            showOriginalText ? "Originaltext ausblenden" : "Originaltext anzeigen",
                            systemImage: "text.quote"
                        )
                        Spacer()
                        Image(systemName: showOriginalText ? "chevron.up" : "chevron.down")
                    }
                    .frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.bordered)

                if showOriginalText {
                    Text(description)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                        .transition(.opacity.combined(with: .move(edge: .top)))
                }
            }
            .cardSurface()
        }
    }

    private func commentsSection(_ recipe: Recipe) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("Gemeinsame Kochnotizen", systemImage: "text.bubble")
                    .font(.title2.bold())
                Spacer()
                if commentsLoading {
                    ProgressView()
                        .controlSize(.small)
                }
            }

            Text("Anzeige auf \(themeStore.commentLanguage.title). Originale bleiben zum Vergleich erhalten.")
                .font(.caption)
                .foregroundStyle(theme.muted)

            if comments.isEmpty && !commentsLoading {
                Text("Noch keine Kochnotiz vorhanden.")
                    .foregroundStyle(theme.muted)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .cardSurface()
            } else {
                ForEach(comments) { comment in
                    RecipeCommentCard(
                        comment: comment,
                        canDelete: !session.readOnly && comment.canDelete
                    ) {
                        Task { await deleteComment(comment) }
                    }
                }
            }

            if let commentError {
                Label(commentError, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(theme.warning)
            }

            if session.readOnly {
                Label("Im Gastzugang können Kochnotizen nur gelesen werden.", systemImage: "eye")
                    .font(.caption)
                    .foregroundStyle(theme.muted)
            } else {
                VStack(alignment: .trailing, spacing: 10) {
                    TextField(
                        "Kochnotiz schreiben …",
                        text: $commentDraft,
                        axis: .vertical
                    )
                    .lineLimit(2...6)
                    .textFieldStyle(.roundedBorder)

                    Button {
                        Task { await postComment(recipeID: recipe.id) }
                    } label: {
                        Label(
                            commentPosting ? "Wird gespeichert …" : "Notiz teilen",
                            systemImage: "paperplane.fill"
                        )
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(theme.accent)
                    .foregroundStyle(theme.ink)
                    .disabled(
                        commentPosting
                            || commentDraft.trimmingCharacters(
                                in: .whitespacesAndNewlines
                            ).isEmpty
                    )
                }
                .cardSurface()
            }
        }
    }

    private func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            recipe = try await session.api.recipe(id: recipeID)
            showOriginalText = false
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func loadComments() async {
        let requestedLanguage = themeStore.commentLanguage
        let requestID = UUID()
        commentRequestID = requestID
        commentsLoading = true
        commentError = nil
        defer {
            if commentRequestID == requestID {
                commentsLoading = false
            }
        }
        do {
            let response = try await session.api.recipeComments(
                id: recipeID,
                language: requestedLanguage
            )
            guard commentRequestID == requestID,
                  response.language == themeStore.commentLanguage.rawValue else {
                return
            }
            comments = response.items
        } catch {
            if commentRequestID == requestID {
                commentError = error.localizedDescription
            }
        }
    }

    private func postComment(recipeID: Int) async {
        let body = commentDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty, !session.readOnly, !commentPosting else { return }
        commentPosting = true
        commentError = nil
        defer { commentPosting = false }
        do {
            _ = try await session.api.createRecipeComment(
                id: recipeID,
                body: body,
                sourceLanguage: themeStore.commentLanguage
            )
            commentDraft = ""
            await loadComments()
        } catch {
            commentError = error.localizedDescription
            session.handle(error)
        }
    }

    private func deleteComment(_ comment: RecipeComment) async {
        guard !session.readOnly, comment.canDelete else { return }
        commentError = nil
        do {
            _ = try await session.api.deleteRecipeComment(
                recipeID: recipeID,
                commentID: comment.id
            )
            comments.removeAll { $0.id == comment.id }
        } catch {
            commentError = error.localizedDescription
            session.handle(error)
        }
    }

    private func addToCart() async {
        do {
            _ = try await session.api.addRecipeToCart(id: recipeID)
            withAnimation(.snappy) { cartConfirmation = true }
            try? await Task.sleep(for: .seconds(2))
            withAnimation(.snappy) { cartConfirmation = false }
        } catch {
            session.handle(error)
        }
    }

    private func toggleFavorite() async {
        do {
            _ = try await session.api.toggleFavorite(id: recipeID)
            await load()
        } catch {
            session.handle(error)
        }
    }

    private func deleteRecipe() async {
        guard recipe != nil, !isDeleting else { return }
        isDeleting = true
        defer { isDeleting = false }
        do {
            _ = try await session.api.deleteRecipe(id: recipeID)
            NotificationCenter.default.post(name: .recipesChanged, object: recipeID)
            dismiss()
        } catch {
            session.handle(error)
        }
    }

    private func safeExternalURL(_ raw: String?) -> URL? {
        guard let raw, let url = URL(string: raw),
              ["https", "http"].contains(url.scheme?.lowercased()) else {
            return nil
        }
        return url
    }

    private func sourceName(_ url: URL) -> String {
        let host = (url.host ?? "Webseite").lowercased()
        if host.contains("pinterest") || host == "pin.it" { return "Pinterest" }
        if host.contains("youtube") || host == "youtu.be" { return "YouTube" }
        if host.contains("tiktok") { return "TikTok" }
        if host.contains("instagram") { return "Instagram" }
        return host.replacingOccurrences(of: "www.", with: "")
    }

    private func imageStatusLabel(_ status: String) -> String {
        switch status {
        case "ok": "Generiert"
        case "pending", "running": "Wird generiert"
        case "backed_up": "Original gesichert"
        case "restored": "Original wiederhergestellt"
        case "error": "Generierung fehlgeschlagen"
        default: status
        }
    }

    private func shareText(_ recipe: Recipe) -> String {
        [recipe.name, recipe.url].compactMap { $0 }.joined(separator: "\n")
    }
}

private struct RecipeCommentCard: View {
    let comment: RecipeComment
    let canDelete: Bool
    let onDelete: () -> Void

    @Environment(\.recipeTheme) private var theme
    @State private var showOriginal = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(comment.createdBy)
                    .font(.subheadline.bold())
                Text(timestamp)
                    .font(.caption)
                    .foregroundStyle(theme.muted)
                Spacer()
                if canDelete {
                    Button(role: .destructive, action: onDelete) {
                        Image(systemName: "trash")
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Eigene Kochnotiz löschen")
                }
            }

            Text(showOriginal ? comment.originalText : comment.text)
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)

            if comment.translated {
                HStack(spacing: 8) {
                    Label(
                        "Automatisch übersetzt aus \(sourceLanguageTitle)",
                        systemImage: "character.bubble"
                    )
                    Spacer()
                    Button(showOriginal ? "Übersetzung" : "Original") {
                        withAnimation(.snappy) { showOriginal.toggle() }
                    }
                }
                .font(.caption)
                .foregroundStyle(theme.muted)
            } else if comment.translationStatus == "unavailable" {
                Label(
                    "Original · Übersetzung vorübergehend nicht verfügbar",
                    systemImage: "exclamationmark.bubble"
                )
                .font(.caption)
                .foregroundStyle(theme.warning)
            }
        }
        .cardSurface()
    }

    private var sourceLanguageTitle: String {
        let code = comment.detectedSourceLanguage ?? comment.sourceLanguage
        return CommentLanguage(rawValue: code)?.title ?? code.uppercased()
    }

    private var timestamp: String {
        Date(timeIntervalSince1970: comment.createdAt)
            .formatted(date: .abbreviated, time: .shortened)
    }
}

private struct StepCard: View {
    let number: Int
    let step: RecipeStep
    @Environment(\.recipeTheme) private var theme
    @State private var remaining: Int?
    @State private var timerTask: Task<Void, Never>?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                Text("\(number)")
                    .font(.headline)
                    .frame(width: 32, height: 32)
                    .background(theme.accent, in: Circle())
                    .foregroundStyle(theme.ink)
                Text(step.instruction)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            if let seconds = step.timerSeconds, seconds > 0 {
                Button {
                    startTimer(seconds)
                } label: {
                    Label(
                        remaining.map { format($0) } ?? "Timer \(format(seconds))",
                        systemImage: remaining == nil ? "timer" : "pause.circle"
                    )
                }
                .buttonStyle(.bordered)
                .padding(.leading, 44)
            }
        }
        .cardSurface()
        .onDisappear { timerTask?.cancel() }
    }

    private func startTimer(_ seconds: Int) {
        if timerTask != nil {
            timerTask?.cancel()
            timerTask = nil
            remaining = nil
            return
        }
        remaining = seconds
        timerTask = Task {
            while let value = remaining, value > 0, !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                if !Task.isCancelled { remaining = max(0, value - 1) }
            }
            timerTask = nil
        }
    }

    private func format(_ seconds: Int) -> String {
        String(format: "%d:%02d", seconds / 60, seconds % 60)
    }
}
