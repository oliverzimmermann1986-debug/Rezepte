import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers

struct AdminView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.openURL) private var openURL
    @State private var overview: AdminOverview?
    @State private var pending: [PendingItem] = []
    @State private var failedDownloads: [FailedDownload] = []
    @State private var importLink = ""
    @State private var isLoading = true
    @State private var isImporting = false
    @State private var resultMessage: String?
    @State private var selectedPending: PendingItem?
    @State private var selectedPhoto: PhotosPickerItem?
    @State private var showFileImporter = false
    @State private var isUploading = false

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

                Section("Foto oder PDF importieren") {
                    PhotosPicker(selection: $selectedPhoto, matching: .images) {
                        Label("Foto aus Mediathek", systemImage: "photo.badge.plus")
                    }
                    .disabled(isUploading)

                    Button {
                        showFileImporter = true
                    } label: {
                        Label("JPG oder PDF aus Dateien", systemImage: "doc.badge.plus")
                    }
                    .disabled(isUploading)

                    if isUploading {
                        HStack {
                            ProgressView()
                            Text("Datei wird analysiert …")
                        }
                    }
                }

                Section("Eingang") {
                    if pending.isEmpty {
                        Label("Keine offenen Einträge", systemImage: "checkmark.circle")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(pending.prefix(50)) { item in
                            Button {
                                selectedPending = item
                            } label: {
                                VStack(alignment: .leading, spacing: 4) {
                                Text(item.displayName)
                                    .fontWeight(.medium)
                                Text(item.url)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                                if let reason = item.reason?.nilIfEmpty {
                                    Text(reason)
                                        .font(.caption)
                                        .foregroundStyle(AppTheme.warning)
                                }
                                Text("Antippen zum Bearbeiten")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                }
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }

                Section("Fehlgeschlagene Downloads") {
                    if failedDownloads.isEmpty {
                        Label("Keine fehlgeschlagenen Downloads", systemImage: "checkmark.circle")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(failedDownloads) { item in
                            VStack(alignment: .leading, spacing: 8) {
                                Text(item.url)
                                    .font(.caption)
                                    .textSelection(.enabled)
                                    .lineLimit(3)
                                Text("\(item.attempts) Versuche · \(item.lastError ?? "Unbekannter Fehler")")
                                    .font(.caption)
                                    .foregroundStyle(AppTheme.warning)
                                HStack {
                                    Button("Erneut versuchen") {
                                        Task { await retry(item) }
                                    }
                                    .buttonStyle(.bordered)
                                    if let url = URL(string: item.url) {
                                        Button("Link öffnen") { openURL(url) }
                                            .buttonStyle(.bordered)
                                    }
                                    Button("Verwerfen", role: .destructive) {
                                        Task { await discard(item) }
                                    }
                                    .buttonStyle(.bordered)
                                }
                                .font(.caption)
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
            .onChange(of: selectedPhoto) { _, item in
                guard let item else { return }
                Task { await uploadPhoto(item) }
            }
            .fileImporter(
                isPresented: $showFileImporter,
                allowedContentTypes: [.pdf, .jpeg, .png],
                allowsMultipleSelection: false
            ) { result in
                guard case let .success(urls) = result, let url = urls.first else {
                    if case let .failure(error) = result { resultMessage = error.localizedDescription }
                    return
                }
                Task { await uploadFile(url) }
            }
            .sheet(item: $selectedPending) { item in
                PendingEditorView(item: item) {
                    await load()
                }
                .environmentObject(session)
            }
        }
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            async let overviewCall = session.api.adminOverview()
            async let pendingCall = session.api.pending()
            async let failedCall = session.api.failedDownloads()
            overview = try await overviewCall
            pending = try await pendingCall
            failedDownloads = try await failedCall
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

    private func uploadPhoto(_ item: PhotosPickerItem) async {
        isUploading = true
        resultMessage = nil
        defer {
            isUploading = false
            selectedPhoto = nil
        }
        do {
            guard let original = try await item.loadTransferable(type: Data.self),
                  let image = UIImage(data: original),
                  let data = image.jpegData(compressionQuality: 0.9) else {
                resultMessage = "Das Foto konnte nicht gelesen werden."
                return
            }
            let result = try await session.api.importFile(
                data: data,
                filename: "rezept-\(Int(Date().timeIntervalSince1970)).jpg",
                mimeType: "image/jpeg"
            )
            resultMessage = result.message ?? "Foto wurde übernommen."
            await load()
        } catch {
            resultMessage = error.localizedDescription
        }
    }

    private func uploadFile(_ url: URL) async {
        isUploading = true
        resultMessage = nil
        defer { isUploading = false }
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            let data = try Data(contentsOf: url)
            let ext = url.pathExtension.lowercased()
            let mimeType = ext == "pdf" ? "application/pdf"
                : (ext == "png" ? "image/png" : "image/jpeg")
            let result = try await session.api.importFile(
                data: data, filename: url.lastPathComponent, mimeType: mimeType
            )
            resultMessage = result.message ?? "Datei wurde übernommen."
            await load()
        } catch {
            resultMessage = error.localizedDescription
        }
    }

    private func retry(_ item: FailedDownload) async {
        do {
            _ = try await session.api.retryFailedDownload(url: item.url)
            resultMessage = "Der Download wird beim nächsten Lauf erneut versucht."
            await load()
        } catch {
            resultMessage = error.localizedDescription
        }
    }

    private func discard(_ item: FailedDownload) async {
        do {
            _ = try await session.api.discardFailedDownload(url: item.url)
            resultMessage = "Der fehlgeschlagene Download wurde verworfen."
            await load()
        } catch {
            resultMessage = error.localizedDescription
        }
    }
}
