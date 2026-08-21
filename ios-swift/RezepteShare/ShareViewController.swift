import Social
import UniformTypeIdentifiers

final class ShareViewController: SLComposeServiceViewController {
    private var sharedURL: String?

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Zu Rezeptregal"
        placeholder = "TikTok- oder Instagram-Link importieren"
        loadSharedURL()
    }

    override func isContentValid() -> Bool {
        guard let sharedURL, let host = URL(string: sharedURL)?.host?.lowercased() else {
            return false
        }
        return host == "tiktok.com" || host.hasSuffix(".tiktok.com")
            || host == "instagram.com" || host.hasSuffix(".instagram.com")
    }

    override func didSelectPost() {
        if let sharedURL, isContentValid() {
            SharedImportQueue.enqueue(sharedURL)
        }
        extensionContext?.completeRequest(returningItems: [], completionHandler: nil)
    }

    override func configurationItems() -> [Any]! { [] }

    private func loadSharedURL() {
        let providers = extensionContext?.inputItems
            .compactMap { $0 as? NSExtensionItem }
            .compactMap(\.attachments)
            .flatMap { $0 } ?? []

        if let provider = providers.first(where: { $0.hasItemConformingToTypeIdentifier(UTType.url.identifier) }) {
            provider.loadItem(forTypeIdentifier: UTType.url.identifier) { [weak self] item, _ in
                let value = (item as? URL)?.absoluteString ?? (item as? String)
                DispatchQueue.main.async {
                    self?.sharedURL = value
                    self?.validateContent()
                }
            }
            return
        }

        if let provider = providers.first(where: { $0.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) }) {
            provider.loadItem(forTypeIdentifier: UTType.plainText.identifier) { [weak self] item, _ in
                let text = item as? String
                let value = text?.split(whereSeparator: { $0.isWhitespace })
                    .map(String.init)
                    .first(where: { $0.hasPrefix("https://") || $0.hasPrefix("http://") })
                DispatchQueue.main.async {
                    self?.sharedURL = value
                    self?.validateContent()
                }
            }
        }
    }
}
