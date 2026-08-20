import SwiftUI

struct CartView: View {
    @EnvironmentObject private var session: SessionStore
    @State private var items: [CartItem] = []
    @State private var newItem = ""
    @State private var isLoading = true
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Group {
                if isLoading && items.isEmpty {
                    ProgressView("Einkaufsliste wird geladen …")
                } else if let errorMessage, items.isEmpty {
                    ErrorState(message: errorMessage) {
                        Task { await load() }
                    }
                } else {
                    List {
                        Section {
                            HStack {
                                TextField("Artikel hinzufügen", text: $newItem)
                                    .submitLabel(.done)
                                    .onSubmit { Task { await add() } }
                                Button {
                                    Task { await add() }
                                } label: {
                                    Image(systemName: "plus.circle.fill")
                                        .font(.title2)
                                }
                                .disabled(newItem.trimmingCharacters(in: .whitespaces).isEmpty)
                                .accessibilityLabel("Artikel hinzufügen")
                            }
                        }

                        if items.isEmpty {
                            Section {
                                EmptyState(
                                    icon: "cart",
                                    title: "Einkaufsliste leer",
                                    message: "Füge einen Artikel oder die Zutaten eines Rezepts hinzu."
                                )
                            }
                        } else {
                            Section("Offen") {
                                ForEach(items.filter { !$0.checked }) { item in
                                    cartRow(item)
                                }
                                .onDelete { offsets in
                                    delete(offsets, from: items.filter { !$0.checked })
                                }
                            }

                            if items.contains(where: \.checked) {
                                Section("Erledigt") {
                                    ForEach(items.filter(\.checked)) { item in
                                        cartRow(item)
                                    }
                                    .onDelete { offsets in
                                        delete(offsets, from: items.filter(\.checked))
                                    }
                                }
                            }
                        }
                    }
                    .listStyle(.insetGrouped)
                    .refreshable { await load() }
                }
            }
            .navigationTitle("Einkauf")
            .toolbar {
                if !items.isEmpty {
                    ToolbarItem(placement: .topBarTrailing) {
                        Menu {
                            Button("Erledigte löschen", systemImage: "checkmark.circle") {
                                Task { await clear(onlyChecked: true) }
                            }
                            Button("Liste leeren", systemImage: "trash", role: .destructive) {
                                Task { await clear(onlyChecked: false) }
                            }
                        } label: {
                            Image(systemName: "ellipsis.circle")
                        }
                    }
                }
            }
            .task { await load() }
        }
    }

    private func cartRow(_ item: CartItem) -> some View {
        Button {
            Task { await toggle(item) }
        } label: {
            HStack(spacing: 12) {
                Image(systemName: item.checked ? "checkmark.circle.fill" : "circle")
                    .font(.title3)
                    .foregroundStyle(item.checked ? Color.green : AppTheme.butter)
                Text(item.displayText)
                    .strikethrough(item.checked)
                    .foregroundStyle(item.checked ? Color.secondary : Color.primary)
                Spacer()
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            items = try await session.api.cart().items
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func add() async {
        let name = newItem.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        do {
            _ = try await session.api.addCartItem(name: name)
            newItem = ""
            await load()
        } catch {
            session.handle(error)
        }
    }

    private func toggle(_ item: CartItem) async {
        do {
            _ = try await session.api.setCartItem(id: item.id, checked: !item.checked)
            await load()
        } catch {
            session.handle(error)
        }
    }

    private func delete(_ offsets: IndexSet, from visibleItems: [CartItem]) {
        let ids = offsets.map { visibleItems[$0].id }
        Task {
            for id in ids {
                do {
                    _ = try await session.api.deleteCartItem(id: id)
                } catch {
                    session.handle(error)
                }
            }
            await load()
        }
    }

    private func clear(onlyChecked: Bool) async {
        do {
            _ = try await session.api.clearCart(onlyChecked: onlyChecked)
            await load()
        } catch {
            session.handle(error)
        }
    }
}
