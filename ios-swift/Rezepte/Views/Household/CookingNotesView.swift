import SwiftUI
import PhotosUI
import ImageIO
import UIKit

@MainActor
struct CookingNotesView: View {
    let recipeID: Int
    var compact = false
    var editHistoryID: Int? = nil
    @EnvironmentObject private var session: SessionStore
    @StateObject private var state = HouseholdFeatureState()
    @State private var notes: [CookingNote] = []
    @State private var editingID: Int?
    @State private var noteText = ""
    @State private var selection: PhotosPickerItem?
    @State private var expanded = false

    private var visibleNotes: [CookingNote] {
        let filtered = compact ? notes.filter { !$0.note.isEmpty || $0.photoUrl != nil } : notes
        return expanded ? filtered : Array(filtered.prefix(compact ? 2 : 5))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if !session.readOnly, session.role != .guest, state.identity == session.identity {
                Text(compact ? "Beim letzten Mal" : "Kochhistorie & Notizen").font(.title3.bold())
                HouseholdFeedback(state: state)
                if !compact {
                    Text("Erfahrungen und Fotos für deinen Haushalt. Bearbeiten können sie die jeweilige Person und Admins.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                if !state.busy && notes.isEmpty && state.error == nil {
                    Text("Nach dem ersten gespeicherten Kochdurchgang kannst du hier eine Notiz und ein Foto ergänzen.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                ForEach(visibleNotes) { item in
                    Divider()
                    VStack(alignment: .leading, spacing: 10) {
                        Text(Date(timeIntervalSince1970: item.cookedAt).formatted(date: .abbreviated, time: .shortened)).font(.headline)
                        Text([item.cookedBy, item.servings.map { "\($0) Portionen" }].compactMap { $0 }.joined(separator: " · "))
                            .font(.caption).foregroundStyle(.secondary)
                        if !item.note.isEmpty { Text(item.note).textSelection(.enabled) }
                        if item.photoUrl != nil {
                            CookingNotePhoto(historyID: item.id, revision: item.updatedAt)
                                .accessibilityLabel("Kochergebnis von \(item.cookedBy)")
                        }
                        if !compact, item.canEdit {
                            if editingID == item.id {
                                TextField("Beim nächsten Mal …", text: $noteText, axis: .vertical)
                                    .lineLimit(3...10).textFieldStyle(.roundedBorder)
                                    .accessibilityLabel("Kochnotiz")
                                Text("\(noteText.count)/4000 Zeichen").font(.caption).foregroundStyle(.secondary)
                                Button("Notiz speichern") { Task { await change(item, kind: .note) } }
                                    .buttonStyle(.borderedProminent).disabled(noteText.count > 4000)
                                PhotosPicker(selection: $selection, matching: .images) {
                                    Label(item.photoUrl == nil ? "Foto hinzufügen" : "Foto ersetzen", systemImage: "photo")
                                        .frame(minHeight: 44)
                                }
                                if item.photoUrl != nil {
                                    Button("Foto entfernen", role: .destructive) { Task { await change(item, kind: .removePhoto) } }
                                }
                                Button("Bearbeiten schließen") { editingID = nil; selection = nil }
                            } else {
                                Button("Notiz oder Foto bearbeiten") { editingID = item.id; noteText = item.note; selection = nil }
                                    .frame(minHeight: 44)
                            }
                        }
                    }
                }
                if notes.count > (compact ? 2 : 5) {
                    Button(expanded ? "Weniger anzeigen" : "Alle Einträge anzeigen") { expanded.toggle() }
                }
                if state.error != nil { Button("Erneut laden") { Task { await load() } } }
            }
        }
        .disabled(state.busy)
        .task(id: "\(session.identity)-\(recipeID)-\(editHistoryID ?? 0)") {
            state.reset(session); notes = []; editingID = nil; selection = nil; noteText = ""; expanded = false
            await load()
        }
        .onChange(of: selection) { _, newValue in
            if let newValue, let item = notes.first(where: { $0.id == editingID }) {
                Task { await change(item, kind: .photo(newValue)) }
            }
        }
    }

    private func load() async {
        guard let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        do {
            let result = try await session.api.cookingNotes(recipeID: recipeID)
            guard state.current(token, session) else { return }
            notes = result
            if let requested = result.first(where: { $0.id == editHistoryID && $0.canEdit }) {
                editingID = requested.id; noteText = requested.note; expanded = true
            }
        } catch { state.failed(error, token: token, session: session) }
    }

    private enum Change { case note, photo(PhotosPickerItem), removePhoto }

    private func change(_ item: CookingNote, kind: Change) async {
        guard item.canEdit, let token = state.begin(session) else { return }
        defer { if state.current(token, session) { selection = nil }; state.finish(token, session) }
        do {
            let result: CookingNote
            switch kind {
            case .note:
                guard noteText.count <= 4000 else { return }
                result = try await session.api.saveCookingNote(historyID: item.id, note: noteText)
            case .removePhoto:
                result = try await session.api.removeCookingPhoto(historyID: item.id)
            case let .photo(photo):
                guard let data = try await photo.loadTransferable(type: Data.self), state.current(token, session) else { return }
                let jpeg = try CookingPhotoEncoding.jpeg(data)
                guard state.current(token, session) else { return }
                result = try await session.api.saveCookingPhoto(historyID: item.id, jpeg: jpeg)
            }
            guard state.current(token, session) else { return }
            notes = notes.map { $0.id == result.id ? result : $0 }
            state.notice = "Gespeichert."
            if case .note = kind { editingID = nil }
        } catch { state.failed(error, token: token, session: session) }
    }
}

enum CookingPhotoEncoding {
    static func jpeg(_ data: Data) throws -> Data {
        guard data.count <= 32 * 1024 * 1024,
              let source = CGImageSourceCreateWithData(data as CFData, nil),
              let thumbnail = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: 1600,
              ] as CFDictionary),
              let jpeg = UIImage(cgImage: thumbnail).jpegData(compressionQuality: 0.82),
              jpeg.count <= 8 * 1024 * 1024 else {
            throw APIError.server(400, "Dieses Foto konnte nicht verarbeitet werden. Bitte wähle ein kleineres Bild.")
        }
        return jpeg
    }
}

@MainActor
private struct CookingNotePhoto: View {
    let historyID: Int
    let revision: Double?
    @EnvironmentObject private var session: SessionStore
    @State private var image: UIImage?
    @State private var failed = false
    private var key: String { "\(session.identity)-\(historyID)-\(revision ?? 0)" }
    var body: some View {
        Group {
            if let image { Image(uiImage: image).resizable().scaledToFit() }
            else if failed { Label("Foto konnte nicht geladen werden", systemImage: "photo") }
            else { ProgressView("Foto wird geladen …") }
        }
        .frame(maxWidth: .infinity, maxHeight: 240)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .task(id: key) {
            let expected = key
            image = nil; failed = false
            do {
                let data = try await session.api.kitchenImage("/api/cook-notes/\(historyID)/photo")
                guard expected == key, !Task.isCancelled else { return }
                image = UIImage(data: data); failed = image == nil
            } catch { if expected == key, !Task.isCancelled { failed = true } }
        }
    }
}
