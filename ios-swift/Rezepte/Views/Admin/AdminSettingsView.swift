import Foundation
import SwiftUI

struct AdminSettingsView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme

    @State private var draft = AdminSettingsDraft()
    @State private var originalDraft = AdminSettingsDraft()
    @State private var storedSecrets = StoredSecretStatus()
    @State private var openAIBaseURL = ""
    @State private var einkaufAPIURL = ""
    @State private var logStats: NativeAdminLogStats?
    @State private var backups: NativeAdminBackupList?
    @State private var isLoading = true
    @State private var isSaving = false
    @State private var isTesting = false
    @State private var isMaintaining = false
    @State private var showSaveConfirmation = false
    @State private var showLogCleanupConfirmation = false
    @State private var statusMessage: String?
    @State private var statusSuccess = true

    private var changedSections: [String] {
        draft.changedSections(comparedTo: originalDraft)
    }

    private var validationMessage: String? {
        draft.validationMessage
    }

    var body: some View {
        Form {
            if let statusMessage {
                Section {
                    Label(
                        statusMessage,
                        systemImage: statusSuccess ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"
                    )
                    .foregroundStyle(statusSuccess ? theme.success : theme.warning)
                }
            }

            Section("Sicherheitsgrenzen") {
                Label("Geheimnisse werden nie ausgelesen. Leere Geheimnisfelder behalten den gespeicherten Wert.", systemImage: "lock.shield")
                    .font(.footnote)
                LabeledContent("OpenAI-Ziel", value: openAIBaseURL.nilIfEmpty ?? "Serverstandard")
                LabeledContent("Einkauf-API", value: einkaufAPIURL.nilIfEmpty ?? "Lokaler Einkaufskorb")
                Text("Ziel-URLs und Serverpfade sind ausschließlich auf dem Server änderbar.")
                    .font(.caption)
                    .foregroundStyle(theme.muted)
            }

            Section("KI & Analyse") {
                TextField("OpenAI-Modell", text: $draft.ai.openAIModel)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                SecureField("Neuen API-Key setzen", text: $draft.ai.openAIKey)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                secretStatus(storedSecrets.openAI, replacement: draft.ai.openAIKey)

                Stepper("Timeout: \(draft.ai.openAITimeout) Sekunden", value: $draft.ai.openAITimeout, in: 5...300, step: 5)
                Toggle("Quelltexte automatisch übersetzen", isOn: $draft.ai.autoTranslate)

                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text("Prüfschwelle")
                        Spacer()
                        Text(draft.ai.confidenceThreshold.formatted(.percent.precision(.fractionLength(0))))
                            .monospacedDigit()
                    }
                    Slider(value: $draft.ai.confidenceThreshold, in: 0...1, step: 0.05)
                }

                DisclosureGroup("Video-Fallback") {
                    Toggle("Aktiv", isOn: $draft.ai.videoFallbackEnabled)
                    Stepper("Maximal \(draft.ai.videoMaxFrames) Frames", value: $draft.ai.videoMaxFrames, in: 1...40)
                    Stepper("Maximal \(draft.ai.videoMaxSeconds) Sekunden", value: $draft.ai.videoMaxSeconds, in: 30...1_800, step: 30)
                    TextField("Transkriptionsmodell", text: $draft.ai.transcriptionModel)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }

                DisclosureGroup("Bildgenerierung") {
                    Toggle("Aktiv", isOn: $draft.ai.imageGenerationEnabled)
                    TextField("Bildmodell", text: $draft.ai.imageModel)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("Größe", text: $draft.ai.imageSize)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Picker("Qualität", selection: $draft.ai.imageQuality) {
                        ForEach(["low", "medium", "high"], id: \.self) { Text($0.capitalized).tag($0) }
                    }
                    Picker("Format", selection: $draft.ai.imageOutputFormat) {
                        ForEach(["jpeg", "png", "webp"], id: \.self) { Text($0.uppercased()).tag($0) }
                    }
                }

                Button {
                    Task { await testOpenAI() }
                } label: {
                    Label(isTesting ? "Verbindung wird getestet …" : "OpenAI-Verbindung testen", systemImage: "network")
                }
                .disabled(isTesting || draft.ai.openAIModel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }

            mailSection(
                title: "Rezept-Postfach",
                account: $draft.recipeMail,
                storedSecret: storedSecrets.recipeMail,
                testAccount: "recipe"
            )

            mailSection(
                title: "Hochzeits-Postfach",
                account: $draft.weddingMail,
                storedSecret: storedSecrets.weddingMail,
                testAccount: "wedding",
                showsWeddingOptions: true
            )

            Section("PDF-Verarbeitung") {
                Toggle("Automatisch drehen", isOn: $draft.pdf.autoRotate)
                Toggle("Tesseract-Ausrichtung", isOn: $draft.pdf.useTesseractOSD)
                Toggle("OCR-Abstimmung", isOn: $draft.pdf.useOCRVote)
                Toggle("Leere Seiten entfernen", isOn: $draft.pdf.removeBlankPages)
                Toggle("Automatisch zuschneiden", isOn: $draft.pdf.autoCrop)
                Toggle("Scans begradigen", isOn: $draft.pdf.deskewScans)
                Toggle("Scans durchsuchbar machen", isOn: $draft.pdf.ocrScans)
                TextField("OCR-Sprachen", text: $draft.pdf.ocrLanguage)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Toggle("Kontrast verbessern", isOn: $draft.pdf.improveContrast)
                Toggle("Scans schärfen", isOn: $draft.pdf.sharpenScans)
                Stepper("Scan-Auflösung: \(draft.pdf.scanDPI) dpi", value: $draft.pdf.scanDPI, in: 150...600, step: 50)
                Toggle("Originaldatei behalten", isOn: $draft.pdf.keepOriginal)
            }

            Section("Automatisierung") {
                TextField("Scraper-Intervall", text: $draft.scraperInterval)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Text("Systemd-OnCalendar, zum Beispiel *:0/30. Änderungen werden nach dem Speichern neu geladen.")
                    .font(.caption)
                    .foregroundStyle(theme.muted)
            }

            Section("Einkauf-Anbindung") {
                SecureField("Neuen App-Token setzen", text: $draft.einkauf.appToken)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                secretStatus(storedSecrets.einkaufToken, replacement: draft.einkauf.appToken)
                TextField("Cloudflare Client-ID", text: $draft.einkauf.cloudflareClientID)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                SecureField("Neues Cloudflare Client-Secret", text: $draft.einkauf.cloudflareClientSecret)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                secretStatus(storedSecrets.einkaufCloudflareSecret, replacement: draft.einkauf.cloudflareClientSecret)
                Toggle("Nach Übertragung konsolidieren", isOn: $draft.einkauf.autoConsolidate)
            }

            Section("Betrieb & Sicherung") {
                if let logStats {
                    LabeledContent("Logdateien", value: logStats.count.formatted())
                    LabeledContent(
                        "Log-Speicher",
                        value: ByteCountFormatter.string(
                            fromByteCount: Int64(logStats.totalBytes),
                            countStyle: .file
                        )
                    )
                    if let retentionDays = logStats.retentionDays {
                        LabeledContent("Aufbewahrung", value: "(retentionDays) Tage")
                    }
                } else {
                    LabeledContent("Logs", value: "Nicht verfügbar")
                }

                if let backups {
                    LabeledContent("DB-Sicherungen", value: backups.allBackups.count.formatted())
                    if let latest = backups.allBackups.first {
                        LabeledContent(
                            "Letzte Sicherung",
                            value: Date(timeIntervalSince1970: latest.mtime).formatted(
                                date: .abbreviated,
                                time: .shortened
                            )
                        )
                        LabeledContent(
                            "Größe",
                            value: ByteCountFormatter.string(
                                fromByteCount: latest.sizeBytes,
                                countStyle: .file
                            )
                        )
                    }
                } else {
                    LabeledContent("DB-Sicherungen", value: "Nicht verfügbar")
                }

                Button {
                    Task { await runBackup() }
                } label: {
                    Label("Datenbank jetzt sichern", systemImage: "externaldrive.badge.plus")
                }
                .disabled(isMaintaining)

                Button(role: .destructive) {
                    showLogCleanupConfirmation = true
                } label: {
                    Label("Alte Logs bereinigen", systemImage: "trash")
                }
                .disabled(isMaintaining || logStats?.exists != true)

                Text("Backups werden neu angelegt. Logs werden erst nach einer zusätzlichen Bestätigung gemäß der serverseitigen Aufbewahrungsfrist gelöscht.")
                    .font(.caption)
                    .foregroundStyle(theme.muted)
            }

            if let validationMessage {
                Section {
                    Label(validationMessage, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(theme.warning)
                }
            }

            if !changedSections.isEmpty {
                Section("Ungespeicherte Änderungen") {
                    Text(changedSections.joined(separator: ", "))
                        .font(.footnote)
                }
            }
        }
        .navigationTitle("Admin-Einstellungen")
        .navigationBarTitleDisplayMode(.inline)
        .scrollContentBackground(.hidden)
        .background(theme.background)
        .overlay {
            if isLoading {
                ProgressView("Einstellungen werden geladen …")
                    .padding(20)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
            }
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Speichern") {
                    showSaveConfirmation = true
                }
                .disabled(changedSections.isEmpty || validationMessage != nil || isSaving || isLoading)
            }
        }
        .confirmationDialog(
            "Änderungen speichern und Serverkonfiguration neu laden?",
            isPresented: $showSaveConfirmation,
            titleVisibility: .visible
        ) {
            Button("Speichern: \(changedSections.joined(separator: ", "))") {
                Task { await save() }
            }
            Button("Abbrechen", role: .cancel) {}
        } message: {
            Text("Nicht aufgeführte Einstellungen, Geheimnisse, Ziel-URLs und Serverpfade bleiben unverändert.")
        }
        .confirmationDialog(
            "Alte Logs wirklich bereinigen?",
            isPresented: $showLogCleanupConfirmation,
            titleVisibility: .visible
        ) {
            Button("Gemäß Aufbewahrungsfrist löschen", role: .destructive) {
                Task { await cleanupLogs() }
            }
            Button("Abbrechen", role: .cancel) {}
        } message: {
            Text("Die betroffenen Logdateien werden dauerhaft gelöscht. Konfiguration, Rezepte und Datenbank-Backups bleiben unberührt.")
        }
        .task { await load() }
        .refreshable { await load() }
    }

    @ViewBuilder
    private func mailSection(
        title: String,
        account: Binding<AdminMailSettingsDraft>,
        storedSecret: Bool,
        testAccount: String,
        showsWeddingOptions: Bool = false
    ) -> some View {
        Section {
            Toggle("Aktiv", isOn: account.enabled)
            TextField("IMAP-Host", text: account.imapHost)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            TextField("IMAP-Port", value: account.imapPort, format: .number)
                .keyboardType(.numberPad)
            TextField("Benutzer / E-Mail", text: account.username)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            SecureField("Neues Passwort / App-Pass", text: account.password)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            secretStatus(storedSecret, replacement: account.wrappedValue.password)
            TextField("Ordner", text: account.folder)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            Stepper("Maximal \(account.wrappedValue.maxMails) Mails", value: account.maxMails, in: 1...500)
            Stepper("Anhänge bis \(account.wrappedValue.attachmentMaxMB) MB", value: account.attachmentMaxMB, in: 1...200)

            if showsWeddingOptions {
                TextField("Standardkategorie", text: account.defaultCategory)
                Toggle("Immer manuell prüfen", isOn: account.alwaysPending)
            }

            Text("Bei einer Änderung von Host oder Port muss das Passwort neu eingegeben werden.")
                .font(.caption)
                .foregroundStyle(theme.muted)

            Button {
                Task { await testMail(testAccount) }
            } label: {
                Label("Gespeicherte Verbindung testen", systemImage: "envelope.badge")
            }
            .disabled(isTesting || !changedSections.isEmpty)
        } header: {
            Text(title)
        }
    }

    @ViewBuilder
    private func secretStatus(_ isStored: Bool, replacement: String) -> some View {
        if !replacement.isEmpty {
            Label("Neuer Wert wird beim Speichern gesetzt", systemImage: "pencil.and.outline")
                .font(.caption)
                .foregroundStyle(theme.warning)
        } else if isStored {
            Label("Geheimnis ist auf dem Server gespeichert", systemImage: "checkmark.shield")
                .font(.caption)
                .foregroundStyle(theme.success)
        } else {
            Label("Noch kein Geheimnis gespeichert", systemImage: "lock.slash")
                .font(.caption)
                .foregroundStyle(theme.muted)
        }
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            async let configRequest = session.api.adminConfiguration()
            async let scheduleRequest = session.api.adminSchedule()
            let (config, schedule) = try await (
                configRequest,
                scheduleRequest
            )
            apply(config, schedule: schedule)
            // Wartungsstatus ist ergänzend: Ein temporär nicht erreichbarer
            // Backup-/Log-Endpunkt darf das Bearbeiten der Konfiguration
            // nicht blockieren.
            logStats = try? await session.api.adminLogStats()
            backups = try? await session.api.adminBackups()
            statusMessage = nil
        } catch {
            statusSuccess = false
            statusMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func apply(_ config: NativeAdminConfig, schedule: NativeAdminScheduleStatus) {
        let loadedDraft = AdminSettingsDraft(
            config: config,
            scraperInterval: schedule.scraper?.oncalendar
        )
        draft = loadedDraft
        originalDraft = loadedDraft
        storedSecrets = StoredSecretStatus(config: config)
        openAIBaseURL = config.ai?.openai?.baseUrl ?? ""
        einkaufAPIURL = config.einkauf?.apiUrl ?? ""
    }

    private func save() async {
        isSaving = true
        defer { isSaving = false }
        do {
            let scheduleChanged = draft.scraperInterval != originalDraft.scraperInterval
            if scheduleChanged {
                let preview = try await session.api.previewAdminSchedule(draft.scraperInterval)
                if preview.scraper?.ok != true {
                    throw APIError.server(
                        400,
                        preview.scraper?.error ?? "Der Zeitplan ist ungültig."
                    )
                }
            }
            _ = try await session.api.updateAdminConfiguration(draft.patch)
            if scheduleChanged {
                _ = try await session.api.updateAdminSchedule(draft.scraperInterval)
            }
            _ = try await session.api.reloadAdminConfiguration()
            let refreshed = try await session.api.adminConfiguration()
            let refreshedSchedule = try await session.api.adminSchedule()
            apply(refreshed, schedule: refreshedSchedule)
            statusSuccess = true
            statusMessage = "Einstellungen gespeichert und neu geladen."
        } catch {
            statusSuccess = false
            statusMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func testOpenAI() async {
        isTesting = true
        defer { isTesting = false }
        do {
            let result = try await session.api.testOpenAIConfiguration(
                apiKey: draft.ai.openAIKey.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty,
                model: draft.ai.openAIModel.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
            )
            statusSuccess = result.ok
            statusMessage = result.displayMessage
        } catch {
            statusSuccess = false
            statusMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func testMail(_ account: String) async {
        isTesting = true
        defer { isTesting = false }
        do {
            let result = try await session.api.testMailConfiguration(account: account)
            statusSuccess = result.ok
            statusMessage = result.displayMessage
        } catch {
            statusSuccess = false
            statusMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func runBackup() async {
        isMaintaining = true
        defer { isMaintaining = false }
        do {
            let result = try await session.api.runAdminBackup()
            statusSuccess = result.ok
            statusMessage = result.displayMessage
            if result.ok {
                backups = try await session.api.adminBackups()
            }
        } catch {
            statusSuccess = false
            statusMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func cleanupLogs() async {
        isMaintaining = true
        defer { isMaintaining = false }
        do {
            let result = try await session.api.cleanupAdminLogs()
            statusSuccess = result.ok
            statusMessage = result.displayMessage
            logStats = try await session.api.adminLogStats()
        } catch {
            statusSuccess = false
            statusMessage = error.localizedDescription
            session.handle(error)
        }
    }
}

private struct StoredSecretStatus {
    var openAI = false
    var recipeMail = false
    var weddingMail = false
    var einkaufToken = false
    var einkaufCloudflareSecret = false

    init() {}

    init(config: NativeAdminConfig) {
        openAI = config.ai?.openai?.apiKey?.nilIfEmpty != nil
        recipeMail = config.mail?.recipe?.password?.nilIfEmpty != nil
        weddingMail = config.mail?.wedding?.password?.nilIfEmpty != nil
        einkaufToken = config.einkauf?.appToken?.nilIfEmpty != nil
        einkaufCloudflareSecret = config.einkauf?.cfAccessClientSecret?.nilIfEmpty != nil
    }
}

private struct AdminSettingsDraft: Equatable {
    var ai = AdminAISettingsDraft()
    var recipeMail = AdminMailSettingsDraft()
    var weddingMail = AdminMailSettingsDraft()
    var pdf = AdminPDFSettingsDraft()
    var scraperInterval = "*:0/30"
    var einkauf = AdminEinkaufSettingsDraft()

    init() {}

    init(config: NativeAdminConfig, scraperInterval: String?) {
        ai = AdminAISettingsDraft(config: config.ai)
        recipeMail = AdminMailSettingsDraft(config: config.mail?.recipe)
        weddingMail = AdminMailSettingsDraft(config: config.mail?.wedding)
        pdf = AdminPDFSettingsDraft(config: config.pdf)
        self.scraperInterval = scraperInterval ?? "*:0/30"
        einkauf = AdminEinkaufSettingsDraft(config: config.einkauf)
    }

    func changedSections(comparedTo original: Self) -> [String] {
        var sections: [String] = []
        if ai != original.ai { sections.append("KI") }
        if recipeMail != original.recipeMail { sections.append("Rezept-Postfach") }
        if weddingMail != original.weddingMail { sections.append("Hochzeits-Postfach") }
        if pdf != original.pdf { sections.append("PDF") }
        if scraperInterval != original.scraperInterval { sections.append("Automatisierung") }
        if einkauf != original.einkauf { sections.append("Einkauf") }
        return sections
    }

    var validationMessage: String? {
        if ai.openAIModel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return "Das OpenAI-Modell darf nicht leer sein."
        }
        for (title, account) in [("Rezept-Postfach", recipeMail), ("Hochzeits-Postfach", weddingMail)] {
            if account.enabled && account.imapHost.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                return "Für \(title) fehlt der IMAP-Host."
            }
            if !(1...65_535).contains(account.imapPort) {
                return "Der IMAP-Port für \(title) ist ungültig."
            }
        }
        if scraperInterval.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return "Das Scraper-Intervall darf nicht leer sein."
        }
        return nil
    }

    var patch: NativeAdminConfigPatch {
        NativeAdminConfigPatch(
            ai: NativeAdminAIConfigPatch(
                openai: NativeAdminOpenAIConfigPatch(
                    apiKey: ai.openAIKey.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty,
                    model: ai.openAIModel.trimmingCharacters(in: .whitespacesAndNewlines),
                    timeout: ai.openAITimeout
                ),
                confidenceThreshold: ai.confidenceThreshold,
                autoTranslate: ai.autoTranslate,
                videoFallback: NativeAdminVideoFallbackConfigPatch(
                    enabled: ai.videoFallbackEnabled,
                    maxFrames: ai.videoMaxFrames,
                    maxSeconds: ai.videoMaxSeconds,
                    transcriptionModel: ai.transcriptionModel.trimmingCharacters(in: .whitespacesAndNewlines)
                ),
                imageGeneration: NativeAdminImageGenerationConfigPatch(
                    enabled: ai.imageGenerationEnabled,
                    model: ai.imageModel.trimmingCharacters(in: .whitespacesAndNewlines),
                    size: ai.imageSize.trimmingCharacters(in: .whitespacesAndNewlines),
                    quality: ai.imageQuality,
                    outputFormat: ai.imageOutputFormat
                )
            ),
            mail: NativeAdminMailConfigPatch(
                recipe: recipeMail.patch(wedding: false),
                wedding: weddingMail.patch(wedding: true)
            ),
            pdf: pdf.patch,
            einkauf: NativeAdminEinkaufConfigPatch(
                appToken: einkauf.appToken.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty,
                cfAccessClientId: einkauf.cloudflareClientID.trimmingCharacters(in: .whitespacesAndNewlines),
                cfAccessClientSecret: einkauf.cloudflareClientSecret.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty,
                autoConsolidate: einkauf.autoConsolidate
            )
        )
    }
}

private struct AdminAISettingsDraft: Equatable {
    var openAIKey = ""
    var openAIModel = "gpt-4o-mini"
    var openAITimeout = 30
    var confidenceThreshold = 0.75
    var autoTranslate = true
    var videoFallbackEnabled = true
    var videoMaxFrames = 10
    var videoMaxSeconds = 600
    var transcriptionModel = "gpt-4o-mini-transcribe"
    var imageGenerationEnabled = true
    var imageModel = "gpt-image-2"
    var imageSize = "1536x1024"
    var imageQuality = "medium"
    var imageOutputFormat = "jpeg"

    init() {}

    init(config: NativeAdminAIConfig?) {
        openAIModel = config?.openai?.model ?? "gpt-4o-mini"
        openAITimeout = config?.openai?.timeout ?? 30
        confidenceThreshold = config?.confidenceThreshold ?? 0.75
        autoTranslate = config?.autoTranslate ?? true
        videoFallbackEnabled = config?.videoFallback?.enabled ?? true
        videoMaxFrames = config?.videoFallback?.maxFrames ?? 10
        videoMaxSeconds = config?.videoFallback?.maxSeconds ?? 600
        transcriptionModel = config?.videoFallback?.transcriptionModel ?? "gpt-4o-mini-transcribe"
        imageGenerationEnabled = config?.imageGeneration?.enabled ?? true
        imageModel = config?.imageGeneration?.model ?? "gpt-image-2"
        imageSize = config?.imageGeneration?.size ?? "1536x1024"
        imageQuality = config?.imageGeneration?.quality ?? "medium"
        imageOutputFormat = config?.imageGeneration?.outputFormat ?? "jpeg"
    }
}

private struct AdminMailSettingsDraft: Equatable {
    var enabled = false
    var imapHost = ""
    var imapPort = 993
    var username = ""
    var password = ""
    var folder = "INBOX"
    var maxMails = 20
    var attachmentMaxMB = 25
    var defaultCategory = "Sonstiges"
    var alwaysPending = true

    init() {}

    init(config: NativeAdminMailAccountConfig?) {
        enabled = config?.enabled ?? false
        imapHost = config?.imapHost ?? ""
        imapPort = config?.imapPort ?? 993
        username = config?.username ?? ""
        folder = config?.folder ?? "INBOX"
        maxMails = config?.maxMails ?? 20
        attachmentMaxMB = config?.attachmentMaxMb ?? 25
        defaultCategory = config?.defaultCategory ?? "Sonstiges"
        alwaysPending = config?.alwaysPending ?? true
    }

    func patch(wedding: Bool) -> NativeAdminMailAccountConfigPatch {
        NativeAdminMailAccountConfigPatch(
            enabled: enabled,
            imapHost: imapHost.trimmingCharacters(in: .whitespacesAndNewlines),
            imapPort: imapPort,
            username: username.trimmingCharacters(in: .whitespacesAndNewlines),
            password: password.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty,
            folder: folder.trimmingCharacters(in: .whitespacesAndNewlines),
            maxMails: maxMails,
            attachmentMaxMb: attachmentMaxMB,
            defaultCategory: wedding ? defaultCategory.trimmingCharacters(in: .whitespacesAndNewlines) : nil,
            alwaysPending: wedding ? alwaysPending : nil
        )
    }
}

private struct AdminPDFSettingsDraft: Equatable {
    var autoRotate = true
    var useTesseractOSD = true
    var useOCRVote = true
    var removeBlankPages = true
    var autoCrop = true
    var deskewScans = true
    var ocrScans = true
    var ocrLanguage = "deu+eng"
    var improveContrast = true
    var sharpenScans = true
    var scanDPI = 300
    var keepOriginal = true

    init() {}

    init(config: NativeAdminPDFConfig?) {
        autoRotate = config?.autoRotate ?? true
        useTesseractOSD = config?.useTesseractOsd ?? true
        useOCRVote = config?.useOcrVote ?? true
        removeBlankPages = config?.removeBlankPages ?? true
        autoCrop = config?.autoCrop ?? true
        deskewScans = config?.deskewScans ?? true
        ocrScans = config?.ocrScans ?? true
        ocrLanguage = config?.ocrLanguage ?? "deu+eng"
        improveContrast = config?.improveContrast ?? true
        sharpenScans = config?.sharpenScans ?? true
        scanDPI = config?.scanDpi ?? 300
        keepOriginal = config?.keepOriginal ?? true
    }

    var patch: NativeAdminPDFConfigPatch {
        NativeAdminPDFConfigPatch(
            autoRotate: autoRotate,
            useTesseractOsd: useTesseractOSD,
            useOcrVote: useOCRVote,
            removeBlankPages: removeBlankPages,
            autoCrop: autoCrop,
            deskewScans: deskewScans,
            ocrScans: ocrScans,
            ocrLanguage: ocrLanguage.trimmingCharacters(in: .whitespacesAndNewlines),
            improveContrast: improveContrast,
            sharpenScans: sharpenScans,
            scanDpi: scanDPI,
            keepOriginal: keepOriginal
        )
    }
}

private struct AdminEinkaufSettingsDraft: Equatable {
    var appToken = ""
    var cloudflareClientID = ""
    var cloudflareClientSecret = ""
    var autoConsolidate = true

    init() {}

    init(config: NativeAdminEinkaufConfig?) {
        cloudflareClientID = config?.cfAccessClientId ?? ""
        autoConsolidate = config?.autoConsolidate ?? true
    }
}
