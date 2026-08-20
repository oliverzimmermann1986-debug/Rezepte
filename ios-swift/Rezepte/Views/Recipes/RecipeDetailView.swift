import SwiftUI

struct RecipeDetailView: View {
    let recipeID: Int

    @EnvironmentObject private var session: SessionStore
    @Environment(\.openURL) private var openURL
    @State private var recipe: Recipe?
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var showIngredientsEditor = false
    @State private var showStepsEditor = false
    @State private var cartConfirmation = false

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
                            .clipShape(RoundedRectangle(cornerRadius: 24))

                        VStack(alignment: .leading, spacing: 8) {
                            Text(recipe.name)
                                .font(.largeTitle.bold())
                                .foregroundStyle(AppTheme.cocoa)
                            if let description = recipe.description, !description.isEmpty {
                                Text(description)
                                    .foregroundStyle(.secondary)
                            }
                        }

                        if recipe.needsManualCare {
                            ManualCareBanner(reasons: recipe.manualCareReasons)
                        }

                        actionBar(recipe)

                        ingredientSection(recipe)
                        stepsSection(recipe)

                        if let sourceURL = safeExternalURL(recipe.url) {
                            VStack(alignment: .leading, spacing: 10) {
                                Text("Quelle")
                                    .font(.title2.bold())
                                Button {
                                    openURL(sourceURL)
                                } label: {
                                    Label("TikTok-/Original-Link öffnen", systemImage: "arrow.up.right.square")
                                        .frame(maxWidth: .infinity, minHeight: 44)
                                }
                                .buttonStyle(.bordered)
                            }
                        }
                    }
                    .padding()
                }
                .background(AppTheme.cream)
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let recipe {
                ToolbarItemGroup(placement: .topBarTrailing) {
                    ShareLink(item: shareText(recipe)) {
                        Image(systemName: "square.and.arrow.up")
                    }
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
        .task { await load() }
    }

    private func actionBar(_ recipe: Recipe) -> some View {
        HStack(spacing: 12) {
            Button {
                Task { await addToCart() }
            } label: {
                Label("Einkaufen", systemImage: "cart.badge.plus")
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.borderedProminent)
            .tint(AppTheme.butter)
            .foregroundStyle(AppTheme.cocoa)

            if let sourceURL = safeExternalURL(recipe.url) {
                Button {
                    openURL(sourceURL)
                } label: {
                    Image(systemName: "link")
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.bordered)
                .accessibilityLabel("Original-Link öffnen")
            }
        }
    }

    private func ingredientSection(_ recipe: Recipe) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Zutaten")
                    .font(.title2.bold())
                Spacer()
                Button("Bearbeiten") { showIngredientsEditor = true }
            }
            if recipe.ingredients.isEmpty {
                Text("Keine Zutaten vorhanden. Bitte manuell ergänzen.")
                    .foregroundStyle(AppTheme.warning)
                    .cardSurface()
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(recipe.ingredients.enumerated()), id: \.offset) { index, ingredient in
                        HStack(alignment: .firstTextBaseline, spacing: 12) {
                            Image(systemName: "circle.fill")
                                .font(.system(size: 6))
                                .foregroundStyle(AppTheme.butter)
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

    private func stepsSection(_ recipe: Recipe) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Zubereitung")
                    .font(.title2.bold())
                Spacer()
                Button("Bearbeiten") { showStepsEditor = true }
            }
            if recipe.steps.isEmpty {
                Text("Keine Schritte vorhanden. Der Quelllink bleibt zur manuellen Pflege erhalten.")
                    .foregroundStyle(AppTheme.warning)
                    .cardSurface()
            } else {
                ForEach(Array(recipe.steps.enumerated()), id: \.offset) { index, step in
                    StepCard(number: index + 1, step: step)
                }
            }
        }
    }

    private func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            recipe = try await session.api.recipe(id: recipeID)
        } catch {
            errorMessage = error.localizedDescription
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

    private func safeExternalURL(_ raw: String?) -> URL? {
        guard let raw, let url = URL(string: raw),
              ["https", "http"].contains(url.scheme?.lowercased()) else {
            return nil
        }
        return url
    }

    private func shareText(_ recipe: Recipe) -> String {
        [recipe.name, recipe.url].compactMap { $0 }.joined(separator: "\n")
    }
}

private struct StepCard: View {
    let number: Int
    let step: RecipeStep
    @State private var remaining: Int?
    @State private var timerTask: Task<Void, Never>?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                Text("\(number)")
                    .font(.headline)
                    .frame(width: 32, height: 32)
                    .background(AppTheme.butter, in: Circle())
                    .foregroundStyle(AppTheme.cocoa)
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
