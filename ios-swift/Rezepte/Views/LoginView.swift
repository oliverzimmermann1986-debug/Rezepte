import SwiftUI

struct LoginView: View {
    @EnvironmentObject private var session: SessionStore
    @State private var server = ""
    @State private var username = ""
    @State private var password = ""
    @State private var cloudflareClientID = ""
    @State private var cloudflareClientSecret = ""
    @State private var showsCloudflareAccess = false
    @State private var isWorking = false
    @State private var errorMessage: String?

    private var canSubmit: Bool {
        !server.trimmingCharacters(in: .whitespaces).isEmpty
            && !username.trimmingCharacters(in: .whitespaces).isEmpty
            && !password.isEmpty
            && !isWorking
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    VStack(alignment: .leading, spacing: 10) {
                        Image(systemName: "fork.knife.circle.fill")
                            .font(.system(size: 58))
                            .foregroundStyle(AppTheme.butter)
                        Text("Deine Rezepte,\nimmer griffbereit.")
                            .font(.largeTitle.bold())
                            .foregroundStyle(AppTheme.cocoa)
                        Text("Melde dich mit deinem Rezepte-Server an.")
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
                            .foregroundStyle(AppTheme.cocoa)
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
                            if isWorking { ProgressView() }
                            Text(isWorking ? "Anmeldung läuft …" : "Anmelden")
                                .fontWeight(.semibold)
                        }
                        .frame(maxWidth: .infinity, minHeight: 48)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(AppTheme.butter)
                    .foregroundStyle(AppTheme.cocoa)
                    .disabled(!canSubmit)

                    Label(
                        "Das Passwort wird nicht gespeichert. Cloudflare-Gerätezugang und Sitzungsschlüssel liegen geschützt im iOS-Schlüsselbund.",
                        systemImage: "lock.shield"
                    )
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                }
                .padding(24)
            }
            .background(AppTheme.cream)
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
        isWorking = true
        errorMessage = nil
        defer { isWorking = false }
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
}
