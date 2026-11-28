# To-do

Ideas and agreed changes that are not built yet. Collected here so several can be done in one update.

## Planned

- **Upvotes (▲) stored per device, like poll votes.**
  Today the "who already upvoted" list for notes and announcements lives only in the server's memory
  (`session_votes` in `portal.py`) and is tied to the login session. After every reload/update, or after
  logging out and in, the same person can upvote again.
  Change: record upvotes in the database per anonymous voter cookie (same `voter` id and `poll_votes`-style
  table as polls), show "already upvoted" from there, and keep the existing `votes` counts.

## Decided against

- **Downvotes on notes.** Small community where people know each other; a downvote reads as rejection.
  Upvotes plus polls cover the need (picking between options).
