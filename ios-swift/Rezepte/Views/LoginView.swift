import SwiftUI

struct LoginView: View {
    private enum LoginAction: Equatable {
        case account
        case guest
    }

    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var server = ""
    @State private var username = ""
    @State private var password = ""
    @State private var cloudflareClientID = ""
    @State private var cloudflareClientSecret = ""
    @State private var showsCloudflareAccess = false
    @State private var workingAction: LoginAction?
    @State private var errorMessage: String?

    private var canSubmit: Bool {
        !server.trimmingCharacters(in: .whitespaces).isEmpty
            && !username.trimmingCharacters(in: .whitespaces).isEmpty
            && !password.isEmpty
            && workingAction == nil
    }

    private var canBrowseAsGuest: Bool {
        !server.trimmingCharacters(in: .whitespaces).isEmpty
            && workingAction == nil
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    VStack(alignment: .leading, spacing: 10) {
                        Image(systemName: "fork.knife.circle.fill")
                            .font(.system(size: 58))
                            .foregroundStyle(theme.accent)
                        Text("Quellen rein.\nLieblingsessen raus.")
                            .font(.largeTitle.bold())
                            .foregroundStyle(theme.ink)
                        Text("Melde dich bei deiner Quellenküche an.")
                            .foregroundStyle(.secondary)
                    }

                    VStack(spacing: 14) {
                        TextField("https://rezepte.example.de", text: $server)
                            .textContentType(.URL)
                            .keyboardType(.URL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .submitLabel(.next)
                        TextField("Benutzername", text: $username)
                            .textContentType(.username)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        SecureField("Passwort", text: $password)
                            .textContentType(.password)
                            .submitLabel(.go)
                            .onSubmit { Task { await signIn() } }
                    }
                    .textFieldStyle(.roundedBorder)

                    DisclosureGroup(isExpanded: $showsCloudflareAccess) {
                        VStack(alignment: .leading, spacing: 12) {
                            TextField("Cloudflare Client-ID", text: $cloudflareClientID)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                            SecureField("Cloudflare Client-Secret", text: $cloudflareClientSecret)
                                .textContentType(.password)
                            Text("Der Gerätezugang wird sicher im iOS-Schlüsselbund gespeichert und bei jeder Serveranfrage an Cloudflare gesendet.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        .padding(.top, 12)
                        .textFieldStyle(.roundedBorder)
                    } label: {
                        Label("Cloudflare-Gerätezugang", systemImage: "shield.lefthalf.filled")
                            .font(.headline)
                            .foregroundStyle(theme.ink)
                    }

                    if let errorMessage {
                        Label(errorMessage, systemImage: "exclamationmark.circle.fill")
                            .font(.subheadline)
                            .foregroundStyle(.red)
                    }

                    Button {
                        Task { await signIn() }
                    } label: {
                        HStack {
                            if workingAction == .account { ProgressView() }
                            Text(workingAction == .account ? "Anmeldung läuft …" : "Anmelden")
                                .fontWeight(.semibold)
                        }
                        .frame(maxWidth: .infinity, minHeight: 48)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(theme.accent)
                    .foregroundStyle(theme.ink)
                    .disabled(!canSubmit)

                    VStack(spacing: 10) {
                        HStack {
                            Divider()
                            Text("oder")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Divider()
                        }

                        Button {
                            Task { await signInAsGuest() }
                        } label: {
                            HStack {
                                if workingAction == .guest { ProgressView() }
                                Label(
                                    workingAction == .guest ? "Gastzugang wird geöffnet …" : "Als Gast ansehen",
                                    systemImage: "eye"
                                )
                                .fontWeight(.semibold)
                            }
                            .frame(maxWidth: .infinity, minHeight: 48)
                        }
                        .buttonStyle(.bordered)
                        .disabled(!canBrowseAsGuest)

                        Text("Im Gastzugang kannst du Rezepte suchen und lesen. Hinzufügen, Bearbeiten, Favoriten, Einkauf und Wochenplanung sind gesperrt.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }

                    Label(
                        "Das Passwort wird nicht gespeichert. Cloudflare-Gerätezugang und Sitzungsschlüssel liegen geschützt im iOS-Schlüsselbund.",
                        systemImage: "lock.shield"
                    )
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                }
                .padding(24)
            }
            .background(theme.background)
            .onAppear {
                if server.isEmpty { server = session.savedServer }
                if cloudflareClientID.isEmpty {
                    cloudflareClientID = session.savedCloudflareClientID
                }
                if cloudflareClientSecret.isEmpty {
                    cloudflareClientSecret = session.savedCloudflareClientSecret
                }
                showsCloudflareAccess = !cloudflareClientID.isEmpty || !cloudflareClientSecret.isEmpty
            }
        }
    }

    private func signIn() async {
        guard canSubmit else { return }
        workingAction = .account
        errorMessage = nil
        defer { workingAction = nil }
        do {
            try await session.signIn(
                server: server,
                username: username,
                password: password,
                cloudflareClientID: cloudflareClientID,
                cloudflareClientSecret: cloudflareClientSecret
            )
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func signInAsGuest() async {
        guard canBrowseAsGuest else { return }
        workingAction = .guest
        errorMessage = nil
        defer { workingAction = nil }
        do {
            try await session.signInAsGuest(
                server: server,
                cloudflareClientID: cloudflareClientID,
                cloudflareClientSecret: cloudflareClientSecret
            )
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
