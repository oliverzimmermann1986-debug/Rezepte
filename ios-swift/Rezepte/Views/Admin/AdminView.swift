import SwiftUI

struct AdminView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.openURL) private var openURL
    @State private var overview: AdminOverview?
    @State private var pending: [PendingItem] = []
    @State private var importLink = ""
    @State private var isLoading = true
    @State private var isImporting = false
    @State private var resultMessage: String?

    var body: some View {
        NavigationStack {
            List {
                if let overview {
                    Section("Überblick") {
                        LabeledContent("Rezepte", value: "\(overview.counts.recipes)")
                        LabeledContent("Manuelle Prüfung", value: "\(overview.counts.pending)")
                        LabeledContent("Fehlgeschlagene Downloads", value: "\(overview.counts.failedDownloads)")
                        LabeledContent("Papierkorb", value: "\(overview.counts.trash)")
                    }
                }

                Section("Link importieren") {
                    TextField("TikTok- oder Instagram-Link", text: $importLink)
                        .textContentType(.URL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button {
                        Task { await importURL() }
                    } label: {
                        if isImporting {
                            Label("Import läuft …", systemImage: "hourglass")
                        } else {
                            Label("Rezept importieren", systemImage: "square.and.arrow.down")
                        }
                    }
                    .disabled(importLink.trimmingCharacters(in: .whitespaces).isEmpty || isImporting)

                    if let resultMessage {
                        Text(resultMessage)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }

                Section("Eingang") {
                    if pending.isEmpty {
                        Label("Keine offenen Einträge", systemImage: "checkmark.circle")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(pending.prefix(20)) { item in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(item.name?.nilIfEmpty ?? "Unbenannter Import")
                                    .fontWeight(.medium)
                                Text(item.url)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                                if let reason = item.reason, !reason.isEmpty {
                                    Text(reason)
                                        .font(.caption)
                                        .foregroundStyle(AppTheme.warning)
                                }
                            }
                        }
                    }
                }

                Section("Automatik") {
                    Button {
                        Task { await runScraper() }
                    } label: {
                        Label("Postfach jetzt prüfen", systemImage: "arrow.clockwise")
                    }
                }

                Section("Konto") {
                    LabeledContent("Angemeldet als", value: session.username)
                    Button {
                        Task {
                            if let url = try? await session.api.privacyURL() {
                                openURL(url)
                            }
                        }
                    } label: {
                        Label("Datenschutz", systemImage: "hand.raised")
                    }
                    Button("Abmelden", role: .destructive) {
                        session.signOut()
                    }
                }
            }
            .overlay {
                if isLoading { ProgressView() }
            }
            .navigationTitle("Verwalten")
            .refreshable { await load() }
            .task { await load() }
        }
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            async let overviewCall = session.api.adminOverview()
            async let pendingCall = session.api.pending()
            overview = try await overviewCall
            pending = try await pendingCall
        } catch {
            session.handle(error)
        }
    }

    private func importURL() async {
        let link = importLink.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: link),
              ["http", "https"].contains(url.scheme?.lowercased()) else {
            resultMessage = "Bitte einen gültigen Link eingeben."
            return
        }
        isImporting = true
        resultMessage = nil
        defer { isImporting = false }
        do {
            let result = try await session.api.importURL(link)
            resultMessage = result.message ?? "Der Link wurde übernommen."
            importLink = ""
            await load()
        } catch {
            resultMessage = error.localizedDescription
        }
    }

    private func runScraper() async {
        do {
            _ = try await session.api.runScraper()
            resultMessage = "Die Postfachprüfung wurde gestartet."
        } catch {
            session.handle(error)
        }
    }
}
