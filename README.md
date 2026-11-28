# Dormitory portal

A small house portal: laundry machines, exchange shelf, guest rooms, house duties, news and rules, activities with shared budget.
Python 3.9 or newer. No packages, no internet connection, no account needed.

## Start

Open this folder in VS Code and press **F5** (choose "Portal (this PC only)"), or in a terminal in this folder:

| System | Command |
| --- | --- |
| Windows | `py portal.py` |
| macOS / Linux | `python3 portal.py` |

The browser opens <http://127.0.0.1:8765>. Stop the server with **Ctrl+C**.

Your existing `demo.sqlite3` is included. Your accounts and passwords keep working; new database columns are added automatically on the first start.
Only on a fresh database does the terminal print newly generated passwords for `residents`, `dienstleiter` and `leitung`. Save them.

| Account | What it can do |
| --- | --- |
| `residents` | Shared resident login (no personal name needed). |
| `dienstleiter` | Service/duty lead: manages Exchange Shelf, House Duties, Washing Machines and Activities. |
| `leitung` | Head administrator: everything, including Guest Room, Rules & News and passwords. |

Use separate browsers (or a private window) to try all three at once.

## Put it online

Live at **https://mariadominika.eu.pythonanywhere.com** (share **/app**). See **SETUP-PYTHONANYWHERE.md** for where everything is, installing changes, backups, the monthly click and the handover checklist.

## Test on your phone (before the site is public)

1. Put the phone on the **same Wi-Fi** as this PC (not mobile data, not a separate guest network).
2. Start the portal with `py portal.py --lan` (macOS/Linux: `python3 portal.py --lan`), or press F5 and choose **"Portal (test on my phone, same Wi-Fi)"**.
3. The terminal prints a line like `On your phone: http://192.168.1.23:8765`. Type that address into the phone's browser.
4. Windows asks about the firewall the first time: allow Python on **private networks**.
5. Optional: in the phone browser use *Add to Home Screen* so the portal opens like an app.

If the phone cannot connect: check the PC's network is set to "Private", turn off any VPN, and check that the router does not isolate Wi-Fi devices ("AP/client isolation").
`--lan` is for testing at home: the connection is not encrypted (http), so do not use it on a shared network.

## Options

`py portal.py --help` shows them all.

| Option | Meaning |
| --- | --- |
| `--lan` | Also accept visitors from your local network (phone test). |
| `--port 9000` | Use another port. |
| `--no-browser` | Do not open a browser window. |
| `--allow-host portal.example.org` | The site name visitors use once it is public (repeatable). |
| `--secure-cookies` | Mark cookies Secure. Use when the site is served over https. |
| `--trust-proxy` | Read the visitor address from `X-Forwarded-For` (only behind your own reverse proxy). |

## Where things are

```
portal.py            the server and all pages (one file)
demo.sqlite3         all data (accounts, photos, bookings, notes)
backups/             automatic copies of the database (made at start, newest 10 kept)
static/style.css     all colors and layout - edit the variables at the top
static/photo.js      forms, calendar, laundry and photo upload
static/app.js        small shared behaviour (kept form input, More menu, live home cards)
static/install.js    "Install the app" page (QR code, install button)
static/qrcode.js     QR code generator (MIT licence, Kazuhiko Arase)
static/sw.js         makes the portal installable, shows an offline page
wsgi.py              connects the portal to a web host (PythonAnywhere)
static/fonts/        Outfit font (SIL Open Font License, see OFL-Outfit.txt)
static/icons/        app icons for "Add to Home Screen"
```

Changes to `style.css` and the scripts show up with a normal browser refresh. Changes to `portal.py` need a restart.

To start with an empty portal, stop the server and move `demo.sqlite3` somewhere else; a fresh database and new passwords are created on the next start.
To go back to an earlier state, stop the server and copy a file from `backups/` over `demo.sqlite3`.

## What is new in this version

- New look: calm neutral background, white cards with soft borders, section colours used as accents, sticky header and a lighter tab bar on phones (light and dark mode).
- Home page: compact "Right now" cards in solid section colours (white text, light on hover). The shelf card shows the two most recently added items. Residents no longer see a guest room card; administrators see "x requests".
- Notes and announcements: the title is italic and text-sized; edit, pin, vote and delete sit behind a small "…" menu in the top-right corner.
- Exchange shelf: "Put back" works for 15 minutes after an item is marked as taken.
- Polls: a note or announcement can include a poll (one answer per line, everyone can tick several). Web addresses in notes and messages are clickable.
- Ready for the internet: runs on PythonAnywhere (wsgi.py), hidden from search engines, "Install the app" page with printable QR poster, offline screen, anonymous usage numbers for the administration.

- Phone: bottom tab bar (Home, Laundry, Shelf, Activities, More), larger tap targets, one-row header, dark mode that follows the phone setting.
- Desktop: one-row navigation that marks the current section in its colour.
- Home page: live "Right now" cards (machines free, newest news, shelf, guest room requests for administrators), refreshed every minute.
- Laundry: one tap starts a machine for 1 h, 1 h 30, ...; custom start time and length stay available.
- Exchange shelf: small thumbnails load first, "Recently taken" with **Put back** instead of deleting immediately.
- Forms: errors say what to check and "Back to the form" keeps what you typed.
- Speed: stylesheet and scripts are cached files, photos are cached, pages are compressed, several visitors are served at the same time.
- Security: too many wrong passwords pause logins for 10 minutes, logins survive a restart (residents stay signed in for 30 days, Dienstleiter 3 days, administrators 8 hours), unexpected host names are refused.
- German is the default when the phone or browser is set to German.
