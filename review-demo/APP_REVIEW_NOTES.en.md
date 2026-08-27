# App Review Notes — Rezepte

Rezepte is a private, self-hosted recipe library. The iOS app requires an HTTPS
server and a user account. A dedicated review server with artificial data is
available for App Review; it is isolated from the developer's production data.

## Review login

- Server: `https://rezepte-review.mausbaeren.me`
- Username: `app-review`
- Password: copy the value from the protected review-credentials file
- Cloudflare device access: leave both optional fields empty

The review account is a normal user account. It can browse recipes, search and
filter the library, open recipe details, change portions, use the guided cooking
view, create a weekly meal plan, and maintain the shared shopping list. It has no
access to production data or server administration.

## Suggested review flow

1. Enter the server, username and password and tap **Anmelden**.
2. Open **Zitronen-Ricotta-Pasta** from the recipe library.
3. Review its image, ingredients and preparation steps. Portions can be changed.
4. Add its ingredients to **Einkauf** and check one shopping item.
5. Open **Wochenplan**. Three artificial recipes are already planned for the
   current week; the combined shopping list can be added from this screen.
6. In **Rezepte**, use the filter **Manuelle Pflege**. The intentionally
   incomplete demo recipe **Sommerliche Tomaten-Galette** demonstrates how the
   app flags a recipe whose ingredients still need manual completion.

All recipe names, text, images, meal-plan entries and shopping items on the
review server are artificial review data. No real household data, third-party
account credentials, social-media cookies, mailboxes or AI-provider credentials
are present on this server.

The privacy notice can be opened from the login screen. The app does not play or
redistribute social-media videos. A source link, when present in a user's own
library, is opened externally by iOS.
