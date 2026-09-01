import Foundation
import SwiftUI
import UIKit

struct AuthenticatedImage: View {
    let recipeID: Int
    let height: CGFloat
    let refreshToken: UUID?
    let cacheVersion: String?

    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var image: UIImage?
    @State private var failed = false

    init(
        recipeID: Int,
        height: CGFloat,
        refreshToken: UUID? = nil,
        cacheVersion: String? = nil
    ) {
        self.recipeID = recipeID
        self.height = height
        self.refreshToken = refreshToken
        self.cacheVersion = cacheVersion
    }

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
            } else {
                ZStack {
                    theme.accentSoft
                    Image(systemName: failed ? "fork.knife" : "photo")
                        .font(.system(size: 30, weight: .medium))
                        .foregroundStyle(theme.ink.opacity(0.7))
                }
            }
        }
        .frame(maxWidth: .infinity)
        .frame(height: height)
        .clipped()
        .task(id: loadIdentity) { await load() }
    }

    private var loadIdentity: String {
        "\(recipeID)-\(refreshToken?.uuidString ?? cacheVersion ?? "cached")"
    }

    private func load() async {
        failed = false
        image = nil
        do {
            let request = try await session.api.imageRequest(
                recipeID: recipeID,
                cacheVersion: refreshToken?.uuidString ?? cacheVersion,
                forceRefresh: refreshToken != nil
            )
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse,
                  (200..<300).contains(http.statusCode),
                  let loaded = UIImage(data: data) else {
                failed = true
                return
            }
            image = loaded
        } catch {
            failed = true
        }
    }
}
