import SwiftUI

struct ShoppingBulkAddView: View {
    private struct Row: Identifiable {
        let id = UUID()
        var name: String
        var amount: String
        var unit: String
    }

    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var shopping: ShoppingSyncStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme
    @State private var text = ""
    @State private var rows: [Row]?
    @State private var errorMessage: String?
    @State private var saved = false
    @State private var identity: UUID
    @State private var householdID: Int?

    init(identity: UUID, householdID: Int?) {
        _identity = State(initialValue: identity)
        _householdID = State(initialValue: householdID)
    }

    var body: some View {
        NavigationStack {
            Form {
                if rows == nil {
                    Section {
                        TextEditor(text: $text)
                            .frame(minHeight: 220)
                            .accessibilityLabel("Mehrere Einkaufsartikel")
                    } header: { Text("Artikel einfügen") } footer: {
                        Text("Eine Zeile je Artikel, zum Beispiel 500 g Tomaten oder 0,5 l Milch. Auch Kommas und Semikolons trennen Artikel. Höchstens 50 Artikel.")
                    }
                    Section {
                        Button("Vorschau prüfen", systemImage: "list.bullet.clipboard") { preview() }
                            .frame(minHeight: 44)
                            .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                } else {
                    Section {
                        ForEach(rows ?? []) { row in
                            draftRow(row)
                        }
                        .onDelete { offsets in rows?.remove(atOffsets: offsets) }
                    } header: { Text("\(rows?.count ?? 0) Artikel prüfen") } footer: {
                        Text("Namen, Mengen und Einheiten kannst du vor dem Hinzufügen ändern. Zum Entfernen nach links wischen.")
                    }
                    Section {
                        Button("Text erneut bearbeiten", systemImage: "text.cursor") { rows = nil; errorMessage = nil }
                        Button("\(rows?.count ?? 0) Artikel hinzufügen", systemImage: "plus.circle.fill") { save() }
                            .fontWeight(.semibold)
                            .frame(minHeight: 44)
                            .disabled(saved || rows?.isEmpty != false || !canWrite)
                    }
                }
                if let errorMessage {
                    Section { Label(errorMessage, systemImage: "exclamationmark.circle").foregroundStyle(.red) }
                }
            }
            .scrollContentBackground(.hidden)
            .background(theme.background)
            .navigationTitle("Mehrere Artikel")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Schließen") { dismiss() } } }
        }
    }

    private var canWrite: Bool {
        session.identity == identity && !session.readOnly && session.role != .guest
            && shopping.householdID == householdID && shopping.isReady
    }

    private func draftRow(_ row: Row) -> some View {
        VStack(spacing: 10) {
            TextField("Artikel", text: binding(row.id, \.name))
                .accessibilityLabel("Artikelname")
            HStack {
                TextField("Menge", text: binding(row.id, \.amount))
                    .keyboardType(.decimalPad)
                    .accessibilityLabel("Menge für \(row.name)")
                Picker("Einheit", selection: binding(row.id, \.unit)) {
                    Text("Ohne Einheit").tag("")
                    ForEach(ShoppingInput.units, id: \.self) { Text($0).tag($0) }
                }
                .labelsHidden()
                .accessibilityLabel("Einheit für \(row.name)")
            }
        }
        .padding(.vertical, 4)
    }

    private func binding(_ id: UUID, _ keyPath: WritableKeyPath<Row, String>) -> Binding<String> {
        Binding(get: { rows?.first(where: { $0.id == id })?[keyPath: keyPath] ?? "" }, set: { value in
            if let index = rows?.firstIndex(where: { $0.id == id }) { rows?[index][keyPath: keyPath] = value }
        })
    }

    private func preview() {
        do {
            rows = try ShoppingInput.parse(text).map { Row(name: $0.name,
                amount: $0.amount.map { String($0).replacingOccurrences(of: ".", with: ",") } ?? "", unit: $0.unit ?? "") }
            errorMessage = nil
        } catch { errorMessage = error.localizedDescription }
    }

    private func save() {
        guard !saved else { return }
        guard canWrite else { errorMessage = "Der Haushalt wurde geändert. Bitte die Artikelliste erneut öffnen."; return }
        do {
            let items = try (rows ?? []).map { ShoppingAddItem(name: $0.name, amount: try ShoppingInput.amount($0.amount), unit: $0.unit.nilIfEmpty) }
            try shopping.addMany(items, expectedHouseholdID: householdID)
            // The synchronous callback has durably saved every row as one
            // transaction. A subsequent network error cannot resubmit them.
            saved = true
            dismiss()
            Task { try? await shopping.refresh() }
        } catch { errorMessage = error.localizedDescription }
    }
}
