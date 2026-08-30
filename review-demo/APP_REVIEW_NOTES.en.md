# App Review Notes — Quellenküche

Quellenküche is a native, source-aware recipe workflow for people who operate
their own HTTPS recipe server. It is not a website wrapper or a collection of
recipe links. A dedicated review server with artificial data is available for
App Review; it is isolated from the developer's production data.

## Review login

- Server: `https://rezepte-review.mausbaeren.me`
- Username: `app-review`
- Password: copy the value from the protected review-credentials file
- Cloudflare device access: leave both optional fields empty

The review account is an administrator of this isolated review instance. It can
browse recipes, search and filter the library, open recipe details, change
portions, use the guided cooking view, create a weekly meal plan, maintain the
shared shopping list, and inspect the in-app **Admin** tab. It has no access to
production data or infrastructure administration.

## Suggested review flow

1. Enter the server, username and password and tap **Anmelden**.
2. Open **Zitronen-Ricotta-Pasta** from the recipe library.
3. Open **Quellenwächter & Rezept-TÜV** in the recipe passport. Review the
   stored source fingerprint, deterministic quality score and the prepared
   source-change diff. Source checks never overwrite recipe data.
4. Review the image, ingredients and preparation steps, then start the guided
   cooking mode. Portions can be changed.
5. Add its ingredients to **Einkauf**. The consolidated list also contains an
   artificial recurring household purchase; duplicate demand is merged.
6. Open **Wochenplan**. Three artificial recipes are already planned for the
   current week; the combined shopping list can be added from this screen.
7. In **Rezepte**, use the filter **Manuelle Pflege**. The intentionally
   incomplete demo recipe **Sommerliche Tomaten-Galette** demonstrates how the
   app flags a recipe whose ingredients still need manual completion.
8. Open **Admin** to inspect the isolated maintenance area. Return to
   **Rezepte**, where **Abmelden** is always available in the upper-right corner.

All recipe names, text, images, meal-plan entries and shopping items on the
review server are artificial review data. No real household data, third-party
account credentials, social-media cookies, mailboxes or AI-provider credentials
are present on this server.

The privacy notice can be opened from the login screen. The app does not play or
redistribute social-media videos. A source link, when present in a user's own
library, is opened externally by iOS.
