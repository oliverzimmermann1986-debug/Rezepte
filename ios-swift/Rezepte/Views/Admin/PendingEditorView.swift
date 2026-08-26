import PhotosUI
import SwiftUI
import UIKit

struct PendingEditorView: View {
    let item: PendingItem
    let onChanged: () async -> Void

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var recipeType: String
    @State private var category: String
    @State private var isSaving = false
    @State private var isPhotoScanning = false
    @State private var selectedPhoto: PhotosPickerItem?
    @State private var scanMessage: String?
    @State private var errorMessage: String?

    init(item: PendingItem, onChanged: @escaping () async -> Void) {
        self.item = item
        self.onChanged = onChanged
        _name = State(initialValue: item.aiSuggestion?.name ?? "")
        _recipeType = State(initialValue: item.aiSuggestion?.type ?? "Hauptgericht")
        _category = State(initialValue: item.aiSuggestion?.category ?? "Allgemein")
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Import") {
                    TextField("Rezeptname", text: $name)
                    TextField("Typ", text: $recipeType)
                    TextField("Kategorie", text: $category)
                }

                if let description = item.description?.nilIfEmpty {
                    Section("Erkannter Text") {
                        Text(description)
                            .font(.callout)
                            .textSelection(.enabled)
                    }
                }

                Section("Foto-Scan") {
                    PhotosPicker(selection: $selectedPhoto, matching: .images) {
                        if isPhotoScanning {
                            Label("Foto wird gescannt …", systemImage: "hourglass")
                        } else {
                            Label("Foto hinzufügen und scannen", systemImage: "photo.badge.plus")
                        }
                    }
                    .disabled(isSaving || isPhotoScanning)
                    Text("Das Foto wird als Rezeptbild gespeichert und erneut auf Zutaten und Schritte geprüft.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if let scanMessage {
                        Text(scanMessage)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                }

                Section("Quelle") {
                    Text(item.url)
                        .font(.caption)
                        .textSelection(.enabled)
                    if let filename = item.aiSuggestion?.filename?.nilIfEmpty {
                        LabeledContent("Datei", value: filename)
                    }
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage)
                            .foregroundStyle(AppTheme.warning)
                    }
                }

                Section {
                    Button(role: .destructive) {
                        Task { await resolve(action: "skip") }
                    } label: {
                        Label("Import verwerfen", systemImage: "trash")
                    }
                }
            }
            .navigationTitle("Import bearbeiten")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Abbrechen") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isSaving ? "Speichert …" : "Speichern") {
                        Task { await resolve(action: "save") }
                    }
                    .disabled(isSaving || name.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .onChange(of: selectedPhoto) { _, photo in
                guard let photo else { return }
                Task { await scanPhoto(photo) }
            }
        }
    }

    private func scanPhoto(_ photo: PhotosPickerItem) async {
        isPhotoScanning = true
        errorMessage = nil
        scanMessage = nil
        defer {
            isPhotoScanning = false
            selectedPhoto = nil
        }
        do {
            guard let original = try await photo.loadTransferable(type: Data.self),
                  let image = UIImage(data: original),
                  let data = image.jpegData(compressionQuality: 0.9) else {
                errorMessage = "Das Foto konnte nicht gelesen werden."
                return
            }
            let result = try await session.api.scanPendingPhoto(
                url: item.url,
                data: data,
                filename: "rezeptfoto-\(Int(Date().timeIntervalSince1970)).jpg",
                mimeType: "image/jpeg"
            )
            guard result.ok != false else {
                errorMessage = result.message ?? "Der Foto-Scan ist fehlgeschlagen."
                return
            }
            scanMessage = result.message ?? "Das Foto wurde erkannt."
            await onChanged()
            if result.action == "auto_saved" || result.action == "already_saved" {
                dismiss()
                return
            }
            if let refreshed = try await session.api.pending().first(where: { $0.url == item.url }) {
                name = refreshed.aiSuggestion?.name ?? name
                recipeType = refreshed.aiSuggestion?.type ?? recipeType
                category = refreshed.aiSuggestion?.category ?? category
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func resolve(action: String) async {
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }
        do {
            let result = try await session.api.resolvePending(
                url: item.url,
                action: action,
                name: name.trimmingCharacters(in: .whitespacesAndNewlines),
                type: recipeType.trimmingCharacters(in: .whitespacesAndNewlines),
                category: category.trimmingCharacters(in: .whitespacesAndNewlines)
            )
            guard result.ok != false else {
                errorMessage = result.message ?? "Der Import konnte nicht gespeichert werden."
                return
            }
            await onChanged()
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
