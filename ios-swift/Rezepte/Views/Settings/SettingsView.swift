import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var themeStore: ThemeStore
    @Environment(\.recipeTheme) private var theme
    @State private var showAdministration = false
    @StateObject private var aiConsent = AIConsentCoordinator()
    @State private var isEnablingLinkImports = false
    @AppStorage("content-language-v1") private var contentLanguage = ContentLanguage.de.rawValue

    var body: some View {
        NavigationStack {
            List {
                if session.readOnly {
                    Section("Gastzugang") {
                        Label("Nur lesen", systemImage: "eye")
                            .foregroundStyle(theme.accent)
                        Text("Rezepte können gesucht und angesehen werden. Änderungen, Importe, Favoriten, Einkauf und Planung bleiben gesperrt.")
                            .font(.caption)
                            .foregroundStyle(theme.muted)
                    }
                }

                Section {
                    VStack(alignment: .leading, spacing: 14) {
                        Text("Farbwelt")
                            .font(.headline)
                        Text("Die Auswahl gilt auf diesem Gerät. Pflaume ist die wiedererkennbare Standardfarbe.")
                            .font(.caption)
                            .foregroundStyle(theme.muted)

                        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                            ForEach(ThemeChoice.allCases) { choice in
                                themeButton(choice)
                            }
                        }
                    }
                    .padding(.vertical, 6)
                }

                Section("Darstellung") {
                    Picker("Darstellung", selection: $themeStore.appearance) {
                        ForEach(AppearanceMode.allCases) { mode in
                            Text(mode.title).tag(mode)
                        }
                    }
                    .pickerStyle(.segmented)
                }

                if session.canImport || session.automaticLinkImportsEnabled {
                    automaticLinkImportSettings
                }

                if session.fullAccess {
                    Section("Inhaltssprache") {
                        Picker("Rezepte und Quelltexte", selection: $contentLanguage) {
                            ForEach(ContentLanguage.allCases) { language in
                                Text(language.title).tag(language.rawValue)
                            }
                        }
                        Text("Diese Sprache wird für „Quelltext übersetzen“ verwendet. Die Übersetzung startet erst nach deiner ausdrücklichen Zustimmung zur KI-Verarbeitung. Der Originaltext bleibt erhalten.")
                            .font(.caption)
                            .foregroundStyle(theme.muted)
                    }
                }

                if let warning = session.compatibilityWarning {
                    Section("Serverabgleich") {
                        Label(warning, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(theme.warning)
                    }
                } else if !session.serverVersion.isEmpty {
                    Section("Serverabgleich") {
                        LabeledContent("Serverversion", value: session.serverVersion)
                        Label("App und Server sind funktionskompatibel", systemImage: "checkmark.seal.fill")
                            .foregroundStyle(theme.success)
                    }
                }

                if session.fullAccess {
                    Section("Verwaltung") {
                        Button {
                            showAdministration = true
                        } label: {
                            Label("Administration öffnen", systemImage: "wrench.and.screwdriver")
                        }
                        Text("Prüfwarteschlange, Bildsicherungen, Generierung und fehlgeschlagene Importe.")
                            .font(.caption)
                            .foregroundStyle(theme.muted)
                    }
                }

                if !session.readOnly {
                    Section("Schneller erreichen") {
                        NavigationLink("Siri & Kurzbefehle") { KitchenShortcutsView() }
                    }
                }

                Section("Konto") {
                    if session.canManageOwnAccount, session.supports("account-management-v1") {
                        NavigationLink("Mein Konto") { AccountProfileView() }
                    } else if session.supports("household-invitations-v1") {
                        NavigationLink("Mein Haushalt & Einladungen") { HouseholdAccountView() }
                    }
                    LabeledContent("Angemeldet als", value: session.username)
                    LabeledContent("Rolle", value: session.role.title)
                    Button("Abmelden", role: .destructive) { Task { await session.logOut() } }
                        .disabled(session.isEndingSession)
                }

                Section("Rechtliches & Hilfe") {
                    LegalLinksView(server: session.savedServer, accessibilityPrefix: "settings.legal")
                }

                Section {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Quellenküche")
                            .font(.headline)
                        Text("Rezepte aus Weblinks, Social Media, Fotos und PDFs – mit sichtbarer Quelle und manueller Prüfung.")
                            .font(.caption)
                            .foregroundStyle(theme.muted)
                    }
                    .padding(.vertical, 4)
                }
            }
            .scrollContentBackground(.hidden)
            .background(theme.background)
            .navigationTitle("Einstellungen")
            .aiConsentPrompt(aiConsent, allowsOneTime: false)
            .fullScreenCover(isPresented: Binding(
                get: { showAdministration && session.fullAccess },
                set: { showAdministration = $0 }
            )) {
                AdminView(presented: true)
                    .environmentObject(session)
                    .environmentObject(themeStore)
                    .environment(\.recipeTheme, themeStore.theme)
                    .tint(themeStore.theme.accent)
                    .preferredColorScheme(themeStore.appearance.colorScheme)
            }
        }
    }

    private var automaticLinkImportSettings: some View {
        Section("Automatischer Linkimport") {
            LabeledContent("Freigabe für OpenAI", value: session.automaticLinkImportsEnabled ? "Aktiviert" : "Ausgeschaltet")
            Text("Gilt auf diesem Gerät für dein Konto und diesen Server. Geteilte Links werden beim Öffnen der App automatisch verarbeitet. Andere KI-Aktionen sowie Foto- und PDF-Uploads fragen weiterhin nach. Abmelden oder ein Haushaltswechsel löscht diese Freigabe.")
                .font(.caption)
                .foregroundStyle(theme.muted)
            if session.automaticLinkImportsEnabled {
                Button("Freigabe widerrufen", role: .destructive) { session.revokeLinkImportConsent() }
                    .accessibilityIdentifier("settings.link-imports.revoke")
                Text("Bereits vom Server angenommene Importe können noch fertiggestellt werden. Wartende Links bleiben auf diesem Gerät.")
                    .font(.caption)
                    .foregroundStyle(theme.muted)
            } else {
                Button("Automatischen Linkimport aktivieren") {
                    Task { await enableAutomaticLinkImports() }
                }
                .disabled(!session.canEnableAutomaticLinkImports || isEnablingLinkImports || session.isProcessingSharedImports)
                .accessibilityIdentifier("settings.link-imports.enable")
                if !session.canEnableAutomaticLinkImports {
                    Text("Zum Aktivieren muss die Verbindung mit deinem Konto und Server bestätigt sein.")
                        .font(.caption).foregroundStyle(theme.muted)
                }
            }
            if session.isProcessingSharedImports {
                ProgressView("Geteilte Links werden übergeben …")
            }
        }
    }

    private func enableAutomaticLinkImports() async {
        guard session.canEnableAutomaticLinkImports, !isEnablingLinkImports,
              !session.isProcessingSharedImports else { return }
        isEnablingLinkImports = true
        defer { isEnablingLinkImports = false }
        guard let _ = await aiConsent.request(.importLink, session: session),
              session.automaticLinkImportsEnabled else { return }
        await session.drainSharedImports()
    }

    private func themeButton(_ choice: ThemeChoice) -> some View {
        let palette = choice.theme
        let selected = themeStore.selection == choice
        return Button {
            withAnimation(.snappy) { themeStore.selection = choice }
        } label: {
            HStack(spacing: 10) {
                Circle()
                    .fill(palette.accent)
                    .frame(width: 25, height: 25)
                    .overlay {
                        if selected {
                            Image(systemName: "checkmark")
                                .font(.caption.bold())
                                .foregroundStyle(palette.ink)
                        }
                    }
                VStack(alignment: .leading, spacing: 1) {
                    Text(choice.title).font(.subheadline.bold())
                    Text(choice.subtitle).font(.caption2).foregroundStyle(theme.muted)
                }
                Spacer(minLength: 0)
            }
            .padding(10)
            .frame(maxWidth: .infinity, minHeight: 58)
            .background(selected ? palette.accentSoft : theme.surface, in: RoundedRectangle(cornerRadius: 13))
            .overlay {
                RoundedRectangle(cornerRadius: 13)
                    .stroke(selected ? palette.accent : theme.outline, lineWidth: selected ? 2 : 1)
            }
        }
        .buttonStyle(.plain)
        .accessibilityValue(selected ? "Ausgewählt" : "")
    }
}
