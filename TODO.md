# To-do

Ideas and agreed changes that are not built yet. Collected here so several can be done in one update.

## Planned

- **Upvotes (▲) stored per device, like poll votes.**
  Today the "who already upvoted" list for notes and announcements lives only in the server's memory
  (`session_votes` in `portal.py`) and is tied to the login session. After every reload/update, or after
  logging out and in, the same person can upvote again.
  Change: record upvotes in the database per anonymous voter cookie (same `voter` id and `poll_votes`-style
  table as polls), show "already upvoted" from there, and keep the existing `votes` counts.

- **Admin page: "Nutzung" (usage numbers) last and folded away.**
  On "Passwörter & Zugang" the usage box is currently the first section and always open, which gets in the way.
  Change: move it to the end of the page as a collapsible section (like the other drawers): only the title
  "Nutzung" shows, and the numbers expand when it is tapped. (In `portal.py`, the `/admin` page: remove the
  `usage_html` panel at the top and append it as a `drawer-box` after the account forms.)

- **"Dienstleiter" in English on the English version.**
  The English texts still say "Dienstleiter" (header badge, login page "Dienstleiter Access", the switch links
  on the login page, "Change Dienstleiter Password", "Change and log out Dienstleiter", "Back to Dienstleiter").
  Change: use an English word in all of these, e.g. **"Duty Manager"** (alternatives: "Duty Lead",
  "House Coordinator"; confirm the wording before building). Only the displayed text changes: the login
  account name stays `dienstleiter`, and the German version keeps "Dienstleiter".

## Decided against

- **Downvotes on notes.** Small community where people know each other; a downvote reads as rejection.
  Upvotes plus polls cover the need (picking between options).
