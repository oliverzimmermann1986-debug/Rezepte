import SwiftUI

struct RecipeFiltersView: View {
    private static let ingredientGroupOrder = [
        "Obst & Gemüse",
        "Fleisch & Fisch",
        "Kühlregal",
        "Vorrat & Konserven",
        "Bäckerei",
        "Getränke",
        "Tiefkühl",
        "Drogerie & Haushalt",
        "Sonstiges",
    ]

    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme

    let facets: RecipeFacets
    let loadMatchCount: (RecipeFilters) async throws -> Int
    let onApply: (RecipeFilters) -> Void

    @State private var draft: RecipeFilters
    @State private var ingredientSearch = ""
    @State private var showPantryBasics = false
    @State private var expandedIngredientGroups: Set<String>
    @State private var matchCount: Int?
    @State private var isRefreshingCount = false
    @State private var countFailed = false

    init(
        filters: RecipeFilters,
        facets: RecipeFacets,
        initialMatchCount: Int,
        loadMatchCount: @escaping (RecipeFilters) async throws -> Int,
        onApply: @escaping (RecipeFilters) -> Void
    ) {
        self.facets = facets
        self.loadMatchCount = loadMatchCount
        self.onApply = onApply
        _draft = State(initialValue: filters)
        _matchCount = State(initialValue: initialMatchCount)
        let selectedNames = filters.includedIngredients.union(filters.excludedIngredients)
        let selectedGroups = Set(
            facets.ingredients
                .filter { selectedNames.contains($0.canonicalName) }
                .map(\.groupName)
        )
        _expandedIngredientGroups = State(
            initialValue: selectedGroups.union(["Obst & Gemüse"])
        )
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 28) {
                    quickFilters
                    classificationFilters

                    if !allergenTags.isEmpty {
                        allergenFilters
                    }

                    if !generalTags.isEmpty {
                        tagFilters
                    }

                    ingredientFilters
                }
                .padding(.horizontal, 20)
                .padding(.top, 18)
                .padding(.bottom, 24)
            }
            .background(theme.background)
            .navigationTitle(
                draft.activeCount == 0
                    ? "Rezepte filtern"
                    : "\(draft.activeCount) Filter aktiv"
            )
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Abbrechen") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if draft.activeCount > 0 {
                        Button("Zurücksetzen") {
                            draft = RecipeFilters()
                            ingredientSearch = ""
                            showPantryBasics = false
                        }
                    }
                }
            }
            .safeAreaInset(edge: .bottom) {
                applyBar
            }
            .task(id: draft) {
                await refreshMatchCount()
            }
        }
    }

    private var quickFilters: some View {
        flatSection(
            title: "Schnellfilter",
            systemImage: "bolt.fill"
        ) {
            Toggle("Nur Favoriten", isOn: $draft.favoriteOnly)
            Divider()
            Toggle("Manuell zu pflegen", isOn: $draft.manualOnly)
            Divider()
            HStack {
                Text("Mindestbewertung")
                Spacer()
                Picker("Mindestbewertung", selection: $draft.minRating) {
                    Text("Alle").tag(0)
                    ForEach(1...5, id: \.self) { rating in
                        Text(String(repeating: "★", count: rating)).tag(rating)
                    }
                }
                .labelsHidden()
                .pickerStyle(.menu)
            }
        }
    }

    private var classificationFilters: some View {
        flatSection(
            title: "Einordnung",
            systemImage: "square.grid.2x2"
        ) {
            HStack {
                Text("Typ")
                Spacer()
                Picker("Typ", selection: $draft.type) {
                    Text("Alle Typen").tag("")
                    ForEach(facets.types, id: \.self) { value in
                        Text(value).tag(value)
                    }
                }
                .labelsHidden()
                .pickerStyle(.menu)
            }
            Divider()
            HStack {
                Text("Kategorie")
                Spacer()
                Picker("Kategorie", selection: $draft.category) {
                    Text("Alle Kategorien").tag("")
                    ForEach(facets.categories, id: \.self) { value in
                        Text(value).tag(value)
                    }
                }
                .labelsHidden()
                .pickerStyle(.menu)
            }
        }
    }

    private var allergenFilters: some View {
        flatSection(
            title: "Allergiker-Infos",
            systemImage: "checkmark.shield",
            footer: "Mehrere Angaben können gleichzeitig gewählt werden. Es erscheinen nur Rezepte, die alle ausgewählten Frei-von-Tags tragen. Die Angaben basieren auf den erkannten Zutaten und ersetzen keine medizinische Prüfung."
        ) {
            ForEach(Array(allergenTags.enumerated()), id: \.element.id) { index, tag in
                Toggle(isOn: allergenBinding(tag.id)) {
                    HStack(spacing: 10) {
                        Label(
                            tag.allergenInfo?.title ?? tag.name,
                            systemImage: tag.allergenInfo?.systemImage ?? "checkmark.shield"
                        )
                        Spacer()
                        facetCount(tag.n)
                    }
                }
                if index < allergenTags.count - 1 { Divider() }
            }
        }
    }

    private var tagFilters: some View {
        flatSection(
            title: "Tags & Ernährung",
            systemImage: "tag"
        ) {
            ForEach(Array(generalTags.enumerated()), id: \.element.id) { index, tag in
                Toggle(isOn: tagBinding(tag.id)) {
                    HStack {
                        Text(tag.name)
                        Spacer()
                        facetCount(tag.n)
                    }
                }
                if index < generalTags.count - 1 { Divider() }
            }
        }
    }

    private var ingredientFilters: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Label("Zutaten", systemImage: "carrot")
                    .font(.headline)
                Spacer()
                if selectedIngredientCount > 0 {
                    Text("\(selectedIngredientCount) gewählt")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(theme.muted)
                }
            }

            Text("Wähle direkt, was enthalten sein muss oder ausgeschlossen werden soll.")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            HStack(spacing: 10) {
                Image(systemName: "magnifyingglass")
                    .foregroundStyle(theme.muted)
                TextField("Zutat suchen", text: $ingredientSearch)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                if !ingredientSearch.isEmpty {
                    Button {
                        ingredientSearch = ""
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(theme.muted)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Zutatensuche leeren")
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(theme.background, in: RoundedRectangle(cornerRadius: 12))

            Toggle("Küchengrundlagen anzeigen", isOn: $showPantryBasics)
                .font(.subheadline)

            if ingredientGroups.isEmpty {
                Text("Keine passende Zutat gefunden.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.vertical, 12)
            } else {
                ForEach(Array(ingredientGroups.enumerated()), id: \.element.id) { groupIndex, group in
                    DisclosureGroup(
                        isExpanded: ingredientGroupBinding(group.name)
                    ) {
                        VStack(spacing: 10) {
                            ForEach(Array(group.ingredients.enumerated()), id: \.element.id) { index, ingredient in
                                ingredientRow(ingredient)
                                if index < group.ingredients.count - 1 { Divider() }
                            }
                        }
                        .padding(.top, 10)
                    } label: {
                        HStack(spacing: 10) {
                            Label(group.name, systemImage: ingredientGroupIcon(group.name))
                                .font(.subheadline.weight(.semibold))
                            Spacer()
                            if group.selectedCount > 0 {
                                Text("\(group.selectedCount) gewählt")
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(theme.accent)
                            } else {
                                Text("\(group.ingredients.count)")
                                    .font(.caption.monospacedDigit())
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    if groupIndex < ingredientGroups.count - 1 { Divider() }
                }
            }

            Text("Salz, Pfeffer, Wasser und gängige Öle sind standardmäßig ausgeblendet. Über die Suche oder den Schalter bleiben sie erreichbar.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .cardSurface()
    }

    private var applyBar: some View {
        VStack(spacing: 7) {
            Button {
                onApply(draft)
            } label: {
                HStack(spacing: 10) {
                    if isRefreshingCount {
                        ProgressView()
                            .controlSize(.small)
                    }
                    Text(applyButtonTitle)
                        .fontWeight(.semibold)
                        .monospacedDigit()
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .tint(theme.accent)

            if countFailed {
                Text("Trefferzahl konnte nicht aktualisiert werden.")
                    .font(.caption)
                    .foregroundStyle(theme.warning)
            }
        }
        .padding(.horizontal, 20)
        .padding(.top, 12)
        .padding(.bottom, 8)
        .background(.ultraThinMaterial)
    }

    private func flatSection<Content: View>(
        title: String,
        systemImage: String,
        footer: String? = nil,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(title, systemImage: systemImage)
                .font(.headline)
            VStack(spacing: 10) {
                content()
            }
            .tint(theme.accent)
            if let footer {
                Text(footer)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func ingredientRow(_ ingredient: IngredientFacet) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(ingredient.displayName)
                    .font(.subheadline.weight(.medium))
                Spacer()
                facetCount(ingredient.n)
            }
            HStack(spacing: 22) {
                ingredientChoiceButton(
                    "Mit",
                    ingredient: ingredient.canonicalName,
                    choice: .include
                )
                ingredientChoiceButton(
                    "Ohne",
                    ingredient: ingredient.canonicalName,
                    choice: .exclude
                )
                Spacer()
            }
        }
        .padding(.vertical, 2)
    }

    private func ingredientChoiceButton(
        _ title: String,
        ingredient: String,
        choice: IngredientChoice
    ) -> some View {
        let selected = currentChoice(for: ingredient) == choice
        return Button {
            ingredientBinding(ingredient).wrappedValue = selected ? .any : choice
        } label: {
            Label(title, systemImage: selected ? "checkmark.square.fill" : "square")
                .font(.subheadline.weight(selected ? .semibold : .regular))
                .foregroundStyle(selected ? theme.ink : theme.muted)
        }
        .buttonStyle(.plain)
        .accessibilityValue(selected ? "Ausgewählt" : "Nicht ausgewählt")
    }

    private func facetCount(_ value: Int) -> some View {
        Text("\(value)")
            .font(.caption.monospacedDigit())
            .foregroundStyle(.secondary)
    }

    private var applyButtonTitle: String {
        guard let matchCount else { return "Rezepte anzeigen" }
        return matchCount == 1 ? "1 Rezept anzeigen" : "\(matchCount) Rezepte anzeigen"
    }

    private var selectedIngredientCount: Int {
        draft.includedIngredients.count + draft.excludedIngredients.count
    }

    private var selectedIngredientNames: Set<String> {
        draft.includedIngredients.union(draft.excludedIngredients)
    }

    private var visibleIngredients: [IngredientFacet] {
        let query = ingredientSearch.trimmingCharacters(in: .whitespacesAndNewlines)
        return facets.ingredients.filter { ingredient in
            if !query.isEmpty {
                return ingredient.displayName.localizedCaseInsensitiveContains(query)
                    || ingredient.canonicalName.localizedCaseInsensitiveContains(query)
            }
            if ingredient.isPantryBasic,
               !showPantryBasics,
               !selectedIngredientNames.contains(ingredient.canonicalName) {
                return false
            }
            return true
        }
    }

    private var ingredientGroups: [IngredientGroup] {
        let grouped = Dictionary(grouping: visibleIngredients, by: \.groupName)
        return grouped.map { name, ingredients in
            IngredientGroup(
                name: name,
                ingredients: ingredients.sorted {
                    if $0.n != $1.n { return $0.n > $1.n }
                    return $0.displayName.localizedCaseInsensitiveCompare($1.displayName) == .orderedAscending
                },
                selectedCount: ingredients.filter {
                    selectedIngredientNames.contains($0.canonicalName)
                }.count
            )
        }
        .sorted { lhs, rhs in
            let left = Self.ingredientGroupOrder.firstIndex(of: lhs.name) ?? .max
            let right = Self.ingredientGroupOrder.firstIndex(of: rhs.name) ?? .max
            if left != right { return left < right }
            return lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
        }
    }

    private var allergenTags: [TagFacet] {
        facets.tags
            .filter { $0.allergenInfo != nil }
            .sorted {
                ($0.allergenInfo?.sortIndex ?? .max) < ($1.allergenInfo?.sortIndex ?? .max)
            }
    }

    private var generalTags: [TagFacet] {
        facets.tags.filter { $0.allergenInfo == nil }
    }

    private func refreshMatchCount() async {
        isRefreshingCount = true
        countFailed = false
        do {
            try await Task.sleep(for: .milliseconds(180))
            try Task.checkCancellation()
            let updatedCount = try await loadMatchCount(draft)
            try Task.checkCancellation()
            matchCount = updatedCount
            isRefreshingCount = false
        } catch is CancellationError {
            // Eine neuere Auswahl startet sofort die nächste Zählung.
        } catch {
            isRefreshingCount = false
            countFailed = true
        }
    }

    private func tagBinding(_ id: Int) -> Binding<Bool> {
        Binding(
            get: { draft.tagIDs.contains(id) },
            set: { selected in
                if selected {
                    draft.allergenTagIDs.remove(id)
                    draft.tagIDs.insert(id)
                } else {
                    draft.tagIDs.remove(id)
                }
            }
        )
    }

    private func allergenBinding(_ id: Int) -> Binding<Bool> {
        Binding(
            get: { draft.allergenTagIDs.contains(id) },
            set: { selected in
                if selected {
                    draft.tagIDs.remove(id)
                    draft.allergenTagIDs.insert(id)
                } else {
                    draft.allergenTagIDs.remove(id)
                }
            }
        )
    }

    private func currentChoice(for name: String) -> IngredientChoice {
        if draft.includedIngredients.contains(name) { return .include }
        if draft.excludedIngredients.contains(name) { return .exclude }
        return .any
    }

    private func ingredientBinding(_ name: String) -> Binding<IngredientChoice> {
        Binding(
            get: { currentChoice(for: name) },
            set: { choice in
                draft.includedIngredients.remove(name)
                draft.excludedIngredients.remove(name)
                if choice == .include { draft.includedIngredients.insert(name) }
                if choice == .exclude { draft.excludedIngredients.insert(name) }
            }
        )
    }

    private func ingredientGroupBinding(_ name: String) -> Binding<Bool> {
        Binding(
            get: {
                !ingredientSearch.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    || expandedIngredientGroups.contains(name)
            },
            set: { expanded in
                guard ingredientSearch.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    return
                }
                if expanded {
                    expandedIngredientGroups.insert(name)
                } else {
                    expandedIngredientGroups.remove(name)
                }
            }
        )
    }

    private func ingredientGroupIcon(_ name: String) -> String {
        switch name {
        case "Obst & Gemüse": "leaf"
        case "Fleisch & Fisch": "fish"
        case "Kühlregal": "drop"
        case "Vorrat & Konserven": "shippingbox"
        case "Bäckerei": "birthday.cake"
        case "Getränke": "cup.and.saucer"
        case "Tiefkühl": "snowflake"
        case "Drogerie & Haushalt": "house"
        default: "basket"
        }
    }
}

private struct IngredientGroup: Identifiable {
    let name: String
    let ingredients: [IngredientFacet]
    let selectedCount: Int

    var id: String { name }
}

private enum IngredientChoice: String, Hashable {
    case any
    case include
    case exclude
}
