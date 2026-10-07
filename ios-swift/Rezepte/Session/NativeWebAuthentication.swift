import AuthenticationServices
import CryptoKit
import Foundation
import Security
import UIKit

enum NativeAuthError: LocalizedError {
    case invalidCallback, invalidAuthorizationURL, randomGenerationFailed, alreadyRunning, unavailable

    var errorDescription: String? {
        switch self {
        case .invalidCallback: "Die Anmeldeantwort konnte nicht geprüft werden. Bitte erneut anmelden."
        case .invalidAuthorizationURL: "Der Server hat eine ungültige Anmeldeadresse geliefert."
        case .randomGenerationFailed: "Die sichere Anmeldung konnte nicht vorbereitet werden."
        case .alreadyRunning: "Eine Anmeldung läuft bereits."
        case .unavailable: "Die Anmeldung konnte nicht geöffnet werden. Bitte erneut versuchen."
        }
    }
}

struct NativeAuthProof {
    let verifier: String
    var challenge: String { Self.base64URL(Data(SHA256.hash(data: Data(verifier.utf8)))) }

    static func make() throws -> Self {
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
            throw NativeAuthError.randomGenerationFailed
        }
        return Self(verifier: base64URL(Data(bytes)))
    }

    private static func base64URL(_ data: Data) -> String {
        data.base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
}

enum NativeAuthCallback {
    static let scheme = "de.mausbaeren.rezepte"

    static func code(from url: URL, expectedFlow: String) throws -> String {
        guard !expectedFlow.isEmpty,
              let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.scheme == scheme, parts.host == "auth", parts.path == "/callback",
              parts.user == nil, parts.password == nil, parts.port == nil, parts.fragment == nil else {
            throw NativeAuthError.invalidCallback
        }
        let query = parts.queryItems ?? []
        let flows = query.filter { $0.name == "flow_id" }
        let codes = query.filter { $0.name == "code" }
        let errors = query.filter { $0.name == "error" }
        guard flows.count == 1, flows.first?.value == expectedFlow else { throw NativeAuthError.invalidCallback }
        if codes.isEmpty, errors.count == 1, errors.first?.value == "cancelled" { throw CancellationError() }
        guard errors.isEmpty, codes.count == 1, let code = codes.first?.value, !code.isEmpty else {
            throw NativeAuthError.invalidCallback
        }
        return code
    }

    static func authorizationURL(_ value: String) throws -> URL {
        guard let url = URL(string: value), let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.scheme == "https", let host = parts.host?.lowercased(),
              ["appleid.apple.com", "accounts.google.com"].contains(host),
              parts.user == nil, parts.password == nil,
              parts.port == nil || parts.port == 443, parts.fragment == nil else {
            throw NativeAuthError.invalidAuthorizationURL
        }
        return url
    }
}

@MainActor
protocol NativeAuthenticating: AnyObject {
    func authenticate(url: URL) async throws -> URL
    func cancel()
}

@MainActor
final class NativeWebAuthentication: NSObject, NativeAuthenticating, ASWebAuthenticationPresentationContextProviding {
    private var browser: ASWebAuthenticationSession?
    private var continuation: CheckedContinuation<URL, Error>?
    private var anchor: UIWindow?
    private var operationID: UUID?

    func authenticate(url: URL) async throws -> URL {
        guard browser == nil else { throw NativeAuthError.alreadyRunning }
        try Task.checkCancellation()
        anchor = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
            .filter { $0.activationState == .foregroundActive }
            .flatMap(\.windows).first(where: \.isKeyWindow)
        guard anchor != nil else { throw NativeAuthError.unavailable }
        let operationID = UUID()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                self.operationID = operationID
                self.continuation = continuation
                let browser = ASWebAuthenticationSession(url: url, callbackURLScheme: NativeAuthCallback.scheme) { [weak self] callback, error in
                    Task { @MainActor [weak self] in
                        guard let self else { return }
                        if let error = error as? ASWebAuthenticationSessionError, error.code == .canceledLogin {
                            self.finish(.failure(CancellationError()), operationID: operationID)
                        } else if let error {
                            self.finish(.failure(error), operationID: operationID)
                        } else if let callback {
                            self.finish(.success(callback), operationID: operationID)
                        } else {
                            self.finish(.failure(NativeAuthError.invalidCallback), operationID: operationID)
                        }
                    }
                }
                self.browser = browser
                browser.presentationContextProvider = self
                if !browser.start() { finish(.failure(NativeAuthError.unavailable), operationID: operationID) }
            }
        } onCancel: {
            Task { @MainActor [weak self] in self?.cancel(operationID: operationID) }
        }
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        anchor ?? ASPresentationAnchor()
    }

    func cancel() {
        guard let operationID else { return }
        cancel(operationID: operationID)
    }

    private func cancel(operationID: UUID) {
        guard self.operationID == operationID else { return }
        browser?.cancel()
        finish(.failure(CancellationError()), operationID: operationID)
    }

    private func finish(_ result: Result<URL, Error>, operationID: UUID) {
        guard self.operationID == operationID else { return }
        self.operationID = nil
        let pending = continuation
        continuation = nil
        browser = nil
        anchor = nil
        pending?.resume(with: result)
    }
}
