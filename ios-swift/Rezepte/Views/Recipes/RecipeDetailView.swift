import SwiftUI
import UIKit

struct RecipeDetailView: View {
    let recipeID: Int

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @Environment(\.recipeTheme) private var theme
    @State private var recipe: Recipe?
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var showIngredientsEditor = false
    @State private var showStepsEditor = false
    @State private var showShoppingServings = false
    @State private var shoppingServings = 1
    @State private var isAddingToCart = false
    @State private var cartConfirmation = false
    @State private var showOriginalText = false
    @State private var showDeleteConfirmation = false
    @State private var isDeleting = false
    @State private var imageRefreshToken = UUID()
    @State private var showMetadataEditor = false
    @State private var showShareLinks = false
    @State private var showPDF = false
    @State private var showDuplicatePrompt = false
    @State private var duplicateName = ""
    @State private var isManaging = false
    @State private var translatedDescription: String?
    @State private var sourceCopied = false
    @AppStorage("content-language-v1") private var contentLanguage = ContentLanguage.de.rawValue

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

                        ratingAndNutritionSection(recipe)

                        ingredientSection(recipe)
                        stepsSection(recipe)

                        sourceSection(recipe)

                        originalTextSection(recipe)

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
                    if session.readOnly {
                        ShareLink(item: [recipe.name, recipe.url].compactMap { $0 }.joined(separator: "\n")) {
                            Image(systemName: "square.and.arrow.up")
                        }
                    } else {
                        Button { showShareLinks = true } label: {
                            Image(systemName: "square.and.arrow.up")
                        }
                    }
                    if !session.readOnly {
                        Button {
                            Task { await toggleFavorite() }
                        } label: {
                            Image(systemName: recipe.isFavorite ? "heart.fill" : "heart")
                                .foregroundStyle(recipe.isFavorite ? Color.red : Color.primary)
                        }
                        .accessibilityLabel(recipe.isFavorite ? "Aus Favoriten entfernen" : "Als Favorit speichern")
                        Menu {
                            Button("Rezeptdaten bearbeiten", systemImage: "pencil") {
                                showMetadataEditor = true
                            }
                            Button("Als Variante duplizieren", systemImage: "plus.square.on.square") {
                                duplicateName = "\(recipe.name) – Variante"
                                showDuplicatePrompt = true
                            }
                            Button(
                                recipe.userVerified == true ? "Prüfung zurücknehmen" : "Zutaten als geprüft markieren",
                                systemImage: recipe.userVerified == true ? "checkmark.seal" : "checkmark.seal.fill"
                            ) {
                                Task { await setVerified(recipe.userVerified != true) }
                            }
                            Button("Nährwerte neu berechnen", systemImage: "bolt.heart") {
                                Task { await computeNutrition() }
                            }
                            if recipe.pdfFilename != nil {
                                Button("Original-PDF öffnen", systemImage: "doc.richtext") {
                                    showPDF = true
                                }
                            }
                        } label: {
                            Image(systemName: "ellipsis.circle")
                        }
                    }
                }
            }
        }
        .overlay(alignment: .bottom) {
            if cartConfirmation {
                Label(
                    "Für \(shoppingServings) \(shoppingServings == 1 ? "Portion" : "Portionen") hinzugefügt",
                    systemImage: "checkmark.circle.fill"
                )
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
        .sheet(isPresented: $showMetadataEditor) {
            if let recipe {
                RecipeMetadataEditorView(recipe: recipe) { await load() }
                    .environmentObject(session)
            }
        }
        .sheet(isPresented: $showShareLinks) {
            if let recipe {
                RecipeShareLinksView(recipeID: recipe.id, recipeName: recipe.name)
                    .environmentObject(session)
            }
        }
        .sheet(isPresented: $showPDF) {
            if let recipe {
                PDFPreviewSheet(title: recipe.name) {
                    try await session.api.recipePDF(id: recipe.id)
                }
            }
        }
        .sheet(isPresented: $showShoppingServings) {
            if let recipe, let originalServings = recipe.servings {
                ShoppingServingsSheet(
                    recipeName: recipe.name,
                    originalServings: originalServings,
                    servings: $shoppingServings,
                    isAdding: isAddingToCart
                ) {
                    Task { await addToCart(servings: shoppingServings) }
                }
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
                .interactiveDismissDisabled(isAddingToCart)
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
        .alert("Variante erstellen", isPresented: $showDuplicatePrompt) {
            TextField("Name der Variante", text: $duplicateName)
            Button("Erstellen") { Task { await duplicateRecipe() } }
            Button("Abbrechen", role: .cancel) {}
        } message: {
            Text("Zutaten, Schritte, Tags und Nährwerte werden kopiert; Favorit, Bewertung und Prüfstatus bleiben unabhängig.")
        }
        .task { await load() }
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
                        shoppingServings = recipe.servings ?? 1
                        showShoppingServings = true
                    } label: {
                        Label("Einkaufen", systemImage: "cart.badge.plus")
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.bordered)
                    .disabled(recipe.ingredients.isEmpty || recipe.servings == nil)
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

                if recipe.ingredients.isEmpty {
                    Label(
                        "Für die Einkaufsliste fehlen Zutaten.",
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

            LabeledContent("Rezept-ID") {
                Text("#\(recipe.id)")
                    .font(.body.monospacedDigit())
                    .textSelection(.enabled)
            }

            if let url = safeExternalURL(recipe.url) {
                LabeledContent("Quelle", value: sourceName(url))
            } else {
                LabeledContent("Quelle", value: "Datei oder eigener Eintrag")
            }

            if let sourceAddedAt = recipe.sourceAddedAt {
                LabeledContent(
                    "Importiert",
                    value: Date(timeIntervalSince1970: sourceAddedAt).formatted(
                        date: .abbreviated,
                        time: .shortened
                    )
                )
            }

            if let status = recipe.imageGenerationStatus?.nilIfEmpty {
                LabeledContent("Bildstatus", value: imageStatusLabel(status))
            }

            if let tags = recipe.tags, !tags.isEmpty {
                LabeledContent("Tags", value: tags.map(\.name).joined(separator: ", "))
            }

            if let summary = recipe.cookSummary, summary.count > 0 {
                LabeledContent("Gekocht", value: "\(summary.count)×")
                if let timestamp = summary.lastCookedAt {
                    LabeledContent(
                        "Zuletzt",
                        value: Date(timeIntervalSince1970: timestamp).formatted(date: .abbreviated, time: .shortened)
                    )
                }
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

    @ViewBuilder
    private func sourceSection(_ recipe: Recipe) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Originalquelle")
                .font(.title2.bold())

            if let sourceURL = safeExternalURL(recipe.url) {
                Text(sourceURL.absoluteString)
                    .font(.footnote.monospaced())
                    .foregroundStyle(theme.muted)
                    .textSelection(.enabled)
                    .lineLimit(4)

                HStack(spacing: 10) {
                    Button {
                        openURL(sourceURL)
                    } label: {
                        Label("Öffnen", systemImage: "arrow.up.right.square")
                    }
                    .buttonStyle(.borderedProminent)

                    Button {
                        UIPasteboard.general.string = sourceURL.absoluteString
                        sourceCopied = true
                        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                            sourceCopied = false
                        }
                    } label: {
                        Label(
                            sourceCopied ? "Kopiert" : "Kopieren",
                            systemImage: sourceCopied ? "checkmark" : "doc.on.doc"
                        )
                    }
                    .buttonStyle(.bordered)

                    ShareLink(item: sourceURL) {
                        Label("Teilen", systemImage: "square.and.arrow.up")
                    }
                    .buttonStyle(.bordered)
                }
                .labelStyle(.iconOnly)
            } else {
                Label("Für dieses Rezept ist keine gültige Original-URL gespeichert.", systemImage: "link.badge.plus")
                    .font(.footnote)
                    .foregroundStyle(theme.warning)

                if !session.readOnly {
                    Button("Originalquelle ergänzen") {
                        showMetadataEditor = true
                    }
                    .buttonStyle(.bordered)
                }
            }
        }
        .cardSurface()
        .accessibilityElement(children: .contain)
    }

    private func ratingAndNutritionSection(_ recipe: Recipe) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Bewertung & Nährwerte").font(.title2.bold())
                Spacer()
                if isManaging { ProgressView() }
            }
            HStack(spacing: 8) {
                ForEach(1...5, id: \.self) { value in
                    Button {
                        Task { await setRating(value) }
                    } label: {
                        Image(systemName: value <= (recipe.rating ?? 0) ? "star.fill" : "star")
                            .foregroundStyle(theme.accent)
                    }
                    .disabled(session.readOnly || isManaging)
                    .accessibilityLabel("\(value) Sterne")
                }
            }
            if let calories = recipe.caloriesPerServing {
                LabeledContent("Pro Portion", value: "\(Int(calories.rounded())) kcal")
                HStack {
                    nutrient("Eiweiß", recipe.proteinG)
                    nutrient("Kohlenhydrate", recipe.carbsG)
                    nutrient("Fett", recipe.fatG)
                }
            } else {
                Text("Noch keine Nährwertschätzung vorhanden.")
                    .font(.caption).foregroundStyle(theme.muted)
            }
        }
        .cardSurface()
    }

    private func nutrient(_ label: String, _ value: Double?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption).foregroundStyle(theme.muted)
            Text(value.map { String(format: "%.1f g", $0) } ?? "–")
                .font(.subheadline.bold())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
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
        if let description = (translatedDescription ?? recipe.descriptionOriginal ?? recipe.description)?
            .trimmingCharacters(in: .whitespacesAndNewlines),
           !description.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                Button {
                    withAnimation(.snappy) { showOriginalText.toggle() }
                } label: {
                    HStack {
                        Label(
                            showOriginalText ? "Quelltext ausblenden" : "Quelltext anzeigen",
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

    private func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            recipe = try await session.api.recipe(id: recipeID)
            translatedDescription = nil
            if contentLanguage != ContentLanguage.de.rawValue,
               let source = recipe?.descriptionOriginal ?? recipe?.description,
               !source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                do {
                    translatedDescription = try await session.api.translateRecipeText(
                        id: recipeID,
                        language: contentLanguage,
                        text: source
                    ).translation
                } catch {
                    // Das Rezept bleibt auch ohne optionale KI-Übersetzung lesbar.
                    translatedDescription = nil
                }
            }
            showOriginalText = false
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func addToCart(servings: Int) async {
        guard !isAddingToCart else { return }
        isAddingToCart = true
        defer { isAddingToCart = false }
        do {
            _ = try await session.api.addRecipeToCart(id: recipeID, servings: servings)
            showShoppingServings = false
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

    private func setRating(_ value: Int) async {
        isManaging = true
        defer { isManaging = false }
        do { _ = try await session.api.setRecipeRating(id: recipeID, value: value); await load() }
        catch { session.handle(error) }
    }

    private func setVerified(_ verified: Bool) async {
        isManaging = true
        defer { isManaging = false }
        do { _ = try await session.api.setRecipeVerified(id: recipeID, verified: verified); await load() }
        catch { session.handle(error) }
    }

    private func computeNutrition() async {
        isManaging = true
        defer { isManaging = false }
        do { _ = try await session.api.computeRecipeNutrition(id: recipeID); await load() }
        catch { session.handle(error) }
    }

    private func duplicateRecipe() async {
        let name = duplicateName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        isManaging = true
        defer { isManaging = false }
        do {
            _ = try await session.api.duplicateRecipe(id: recipeID, newName: name)
            NotificationCenter.default.post(name: .recipesChanged, object: nil)
        } catch { session.handle(error) }
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

}

private struct ShoppingServingsSheet: View {
    let recipeName: String
    let originalServings: Int
    @Binding var servings: Int
    let isAdding: Bool
    let onAdd: () -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    VStack(spacing: 6) {
                        Text("Für wie viele Portionen einkaufen?")
                            .font(.title2.bold())
                            .multilineTextAlignment(.center)
                        Text("Die Mengen von \(recipeName) werden vor dem Hinzufügen angepasst.")
                            .font(.callout)
                            .foregroundStyle(theme.muted)
                            .multilineTextAlignment(.center)
                    }

                    ServingPicker(
                        value: $servings,
                        original: originalServings,
                        disabled: isAdding
                    )

                    Button(action: onAdd) {
                        Label(
                            isAdding
                                ? "Wird hinzugefügt …"
                                : "Für \(servings) \(servings == 1 ? "Portion" : "Portionen") hinzufügen",
                            systemImage: "cart.badge.plus"
                        )
                        .frame(maxWidth: .infinity, minHeight: 50)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(theme.accent)
                    .foregroundStyle(theme.ink)
                    .disabled(isAdding)
                }
                .padding()
            }
            .background(theme.background)
            .navigationTitle("Einkauf planen")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Abbrechen") { dismiss() }
                        .disabled(isAdding)
                }
            }
        }
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
