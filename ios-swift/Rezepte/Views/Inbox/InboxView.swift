import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers

struct InboxView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var importLink = ""
    @State private var pending: [PendingItem] = []
    @State private var selectedPending: PendingItem?
    @State private var selectedPhoto: PhotosPickerItem?
    @State private var showFileImporter = false
    @State private var isWorking = false
    @State private var isLoading = false
    @State private var resultMessage: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 24) {
                    hero
                    importComposer

                    if session.fullAccess {
                        reviewQueue
                    } else {
                        Label(
                            "Importe werden automatisch verarbeitet. Unsichere Inhalte prüft die Verwaltung.",
                            systemImage: "checkmark.shield"
                        )
                        .font(.callout)
                        .foregroundStyle(theme.muted)
                        .cardSurface()
                    }
                }
                .padding(.horizontal, 18)
                .padding(.bottom, 42)
            }
            .background(theme.background)
            .navigationTitle("Eingang")
            .refreshable { await loadPending() }
            .task { await loadPending() }
            .onChange(of: selectedPhoto) { _, item in
                guard let item else { return }
                Task { await uploadPhoto(item) }
            }
            .fileImporter(
                isPresented: $showFileImporter,
                allowedContentTypes: [.pdf, .jpeg, .png],
                allowsMultipleSelection: false
            ) { result in
                switch result {
                case let .success(urls):
                    if let url = urls.first { Task { await uploadFile(url) } }
                case let .failure(error):
                    resultMessage = error.localizedDescription
                }
            }
            .sheet(item: $selectedPending) { item in
                PendingEditorView(item: item) {
                    await loadPending()
                }
                .environmentObject(session)
            }
        }
    }

    private var hero: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("AUS QUELLEN WIRD KÜCHE")
                .font(.caption.weight(.semibold))
                .tracking(1.4)
                .foregroundStyle(theme.muted)
            Text("Ein Link genügt.")
                .font(.system(.largeTitle, design: .rounded, weight: .bold))
                .tracking(-0.7)
                .foregroundStyle(theme.ink)
            Text("Webseite, Pinterest, YouTube, TikTok oder Instagram teilen – Quellenküche liest Rezept, Zutaten und Schritte und bewahrt die Herkunft.")
                .font(.body)
                .foregroundStyle(theme.muted)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 8) {
                flowStep("Quelle", symbol: "link")
                Image(systemName: "chevron.right").font(.caption2).foregroundStyle(theme.muted)
                flowStep("Erkennen", symbol: "text.viewfinder")
                Image(systemName: "chevron.right").font(.caption2).foregroundStyle(theme.muted)
                flowStep("Prüfen", symbol: "checkmark.seal")
            }
            .padding(.top, 4)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 16)
    }

    private func flowStep(_ label: String, symbol: String) -> some View {
        Label(label, systemImage: symbol)
            .font(.caption.weight(.medium))
            .foregroundStyle(theme.ink)
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .background(theme.accentSoft, in: RoundedRectangle(cornerRadius: 10))
    }

    private var importComposer: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Rezept einsammeln")
                .font(.title2.bold())
                .foregroundStyle(theme.ink)

            HStack(spacing: 10) {
                Image(systemName: "link")
                    .foregroundStyle(theme.muted)
                TextField("Rezeptlink einfügen", text: $importLink)
                    .textContentType(.URL)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.go)
                    .onSubmit { Task { await importURL() } }
                Button {
                    Task { await importURL() }
                } label: {
                    Image(systemName: isWorking ? "hourglass" : "arrow.down")
                        .font(.headline)
                        .frame(width: 36, height: 36)
                }
                .buttonStyle(.borderedProminent)
                .disabled(importLink.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isWorking)
                .accessibilityLabel("Link importieren")
            }
            .padding(10)
            .background(theme.surface, in: RoundedRectangle(cornerRadius: 14))
            .overlay {
                RoundedRectangle(cornerRadius: 14).stroke(theme.outline)
            }

            HStack(spacing: 10) {
                PhotosPicker(selection: $selectedPhoto, matching: .images) {
                    sourceButton("Foto", symbol: "camera.viewfinder")
                }
                .disabled(isWorking)

                Button { showFileImporter = true } label: {
                    sourceButton("PDF", symbol: "doc.text.viewfinder")
                }
                .disabled(isWorking)
            }

            if let resultMessage {
                Label(resultMessage, systemImage: "info.circle")
                    .font(.callout)
                    .foregroundStyle(theme.muted)
                    .transition(.opacity)
            }
        }
        .padding(18)
        .background(theme.accentSoft.opacity(0.55), in: RoundedRectangle(cornerRadius: 24))
    }

    private func sourceButton(_ title: String, symbol: String) -> some View {
        Label(title, systemImage: symbol)
            .font(.headline)
            .frame(maxWidth: .infinity, minHeight: 48)
            .background(theme.surface, in: RoundedRectangle(cornerRadius: 14))
            .contentShape(Rectangle())
    }

    private var reviewQueue: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Zu prüfen")
                        .font(.title2.bold())
                    Text("Unklare Importe bleiben sichtbar, statt Inhalte zu erfinden.")
                        .font(.caption)
                        .foregroundStyle(theme.muted)
                }
                Spacer()
                if isLoading { ProgressView() }
            }

            if pending.isEmpty && !isLoading {
                Label("Der Eingang ist aufgeräumt", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(theme.success)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .cardSurface()
            } else {
                ForEach(pending.prefix(20)) { item in
                    Button {
                        selectedPending = item
                    } label: {
                        HStack(spacing: 14) {
                            Image(systemName: sourceSymbol(item.url))
                                .font(.title3)
                                .foregroundStyle(theme.ink)
                                .frame(width: 42, height: 42)
                                .background(theme.accentSoft, in: RoundedRectangle(cornerRadius: 12))
                            VStack(alignment: .leading, spacing: 4) {
                                Text(item.displayName)
                                    .font(.headline)
                                    .foregroundStyle(theme.ink)
                                    .lineLimit(1)
                                Text(reviewStatus(item))
                                    .font(.caption)
                                    .foregroundStyle(item.reason == nil ? theme.muted : theme.warning)
                                if let confidence = item.aiSuggestion?.confidence {
                                    ProgressView(value: confidence)
                                        .tint(theme.accent)
                                }
                            }
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.caption.bold())
                                .foregroundStyle(theme.muted)
                        }
                        .cardSurface()
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private func reviewStatus(_ item: PendingItem) -> String {
        if let confidence = item.aiSuggestion?.confidence {
            return "\(Int((confidence * 100).rounded())) % erkannt · Quelle bleibt erhalten"
        }
        return item.reason?.nilIfEmpty ?? "Erkennung wartet auf Prüfung"
    }

    private func sourceSymbol(_ value: String) -> String {
        let lower = value.lowercased()
        if lower.contains("youtube") || lower.contains("youtu.be") { return "play.rectangle.fill" }
        if lower.contains("pinterest") { return "pin.fill" }
        if lower.contains("tiktok") || lower.contains("instagram") { return "play.square.stack.fill" }
        if lower.hasSuffix(".pdf") || lower.contains("manual-upload") { return "doc.fill" }
        return "globe"
    }

    private func loadPending() async {
        guard session.fullAccess else {
            pending = []
            return
        }
        isLoading = true
        defer { isLoading = false }
        do {
            pending = try await session.api.pending()
        } catch {
            resultMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func importURL() async {
        let link = importLink.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: link), ["http", "https"].contains(url.scheme?.lowercased()) else {
            resultMessage = "Bitte einen gültigen Weblink eingeben."
            return
        }
        isWorking = true
        resultMessage = nil
        defer { isWorking = false }
        do {
            let result = try await session.api.importURL(link)
            importLink = ""
            resultMessage = result.message ?? "Der Link wurde in den Eingang gelegt."
            await loadPending()
        } catch {
            resultMessage = error.localizedDescription
        }
    }

    private func uploadPhoto(_ item: PhotosPickerItem) async {
        isWorking = true
        resultMessage = nil
        defer {
            isWorking = false
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
            resultMessage = result.message ?? "Das Foto wurde in den Eingang gelegt."
            await loadPending()
        } catch {
            resultMessage = error.localizedDescription
        }
    }

    private func uploadFile(_ url: URL) async {
        isWorking = true
        resultMessage = nil
        defer { isWorking = false }
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            let data = try Data(contentsOf: url)
            let ext = url.pathExtension.lowercased()
            let mimeType = ext == "pdf" ? "application/pdf" : (ext == "png" ? "image/png" : "image/jpeg")
            let result = try await session.api.importFile(
                data: data,
                filename: url.lastPathComponent,
                mimeType: mimeType
            )
            resultMessage = result.message ?? "Die Datei wurde in den Eingang gelegt."
            await loadPending()
        } catch {
            resultMessage = error.localizedDescription
        }
    }
}
