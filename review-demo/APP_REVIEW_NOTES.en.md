# App Review Notes — Rezeptregal 1.3 candidate

Draft for the next candidate. Verify this walkthrough against the recorded
build and review server before copying it into App Store Connect. This file
does not change the currently submitted version.

Rezeptregal is a native iPhone recipe app for a compatible, privately operated
HTTPS recipe server. Its workflow connects the original recipe source,
user-reviewed corrections and personal cooking experience. It is not a public
recipe marketplace. An isolated review instance with artificial data is
provided below; a reviewer does not need to install or operate a server.

## Review login

- Server: `https://rezepte-review.mausbaeren.me`
- Username: `app-review`
- Password: use the protected App Review sign-in field
- Optional Cloudflare device access: leave both fields empty

The review account is an administrator of this isolated instance. It has no
access to production household data. The current server must advertise
`cooking-memory-v1` and `import-review-v1` before this build is reviewed.

## Suggested review flow

1. Enter the server and credentials, then tap **Anmelden**. The full-access tabs
   are **Eingang**, **Archiv**, **Heute**, **Einkauf** and **Einstellungen**.
2. In **Archiv**, open **Zitronen-Ricotta-Pasta**. Tap **Import fertigstellen**
   at the recipe. It presents structural issues alongside the preserved
   original source and editable ingredients and preparation steps. Confirmed
   corrections from this administrator account update the recipe. A regular
   non-administrator account submits a proposal for administrator approval.
   The existing Source Watcher compares observed source text without
   automatically replacing the saved recipe.
3. Return to the recipe's personal cooking-memory section and tap **Erfahrung
   festhalten**. Add a note, an
   adjustment and a tip for the next time. These entries belong to the signed-in
   user. They do not change the household's recipe. The same reflection is
   available after completing a cooking session.
4. Return to **Archiv** and open the local library, **Offline-Regal**. The
   recipe opened in step 2 is now available locally. Open it and start or resume
   cooking. The local recipe text and progress remain available when offline;
   images and external source pages may still need internet access. Initial
   login and uncached server content require a connection.
5. Optionally inspect **Heute** for the weekly plan and **Einkauf** for the
   consolidated shopping list and recurring household purchases. These are
   existing supporting features, not the new product focus.

For a read-only tour, **Als Gast ansehen** is available on the login screen
after entering the server. Guests cannot create personal cooking notes or make
recipe changes. Administration, when needed, is inside **Einstellungen**; it
is not a separate bottom tab. Sign-out and the privacy link are also in
**Einstellungen**.

All recipe and shopping content on this instance is artificial review data.
The source page for the demo pasta explicitly identifies itself as review
data. The app retains source references; it does not redistribute social-media
videos. Structural checks and substitution suggestions do not certify recipe
accuracy, food safety or freedom from allergens.

An independently recorded walkthrough and fresh native screenshots accompany
the next submission only after validation. No claim of guaranteed approval is
made.
