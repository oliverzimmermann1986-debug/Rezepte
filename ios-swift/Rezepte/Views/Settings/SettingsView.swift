import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var themeStore: ThemeStore
    @Environment(\.recipeTheme) private var theme
    @Environment(\.openURL) private var openURL
    @State private var showAdministration = false

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
                        Text("Die Auswahl gilt auf diesem Gerät. Butter bleibt die wiedererkennbare Standardfarbe.")
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

                Section("Konto") {
                    LabeledContent("Angemeldet als", value: session.username)
                    LabeledContent("Zugriff", value: session.readOnly ? "Nur lesen" : "Bearbeiten")
                    Button {
                        Task {
                            do {
                                let url = try await session.api.privacyURL()
                                openURL(url)
                            } catch {
                                session.handle(error)
                            }
                        }
                    } label: {
                        Label("Datenschutz", systemImage: "hand.raised")
                    }
                    Button("Abmelden", role: .destructive) { session.signOut() }
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
            .fullScreenCover(isPresented: $showAdministration) {
                AdminView(presented: true)
                    .environmentObject(session)
                    .environmentObject(themeStore)
                    .environment(\.recipeTheme, themeStore.theme)
                    .tint(themeStore.theme.accent)
                    .preferredColorScheme(themeStore.appearance.colorScheme)
            }
        }
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
