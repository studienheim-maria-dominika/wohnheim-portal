# Running the portal online (PythonAnywhere, free)

## Where everything is

| What | Where |
| --- | --- |
| **The live portal** | <https://mariadominika.eu.pythonanywhere.com> |
| **Link to share / QR poster** | <https://mariadominika.eu.pythonanywhere.com/app> |
| **Hosting account** | <https://eu.pythonanywhere.com>, username `mariadominika` (EU servers in Frankfurt, free plan) |
| **The code** | <https://github.com/studienheim-maria-dominika/wohnheim-portal> (public: no passwords, no residents' data) |
| **The live data** | the file `/home/mariadominika/claude-dorm-portal/demo.sqlite3` on PythonAnywhere (accounts, photos, bookings, notes) |

Everyone with the link can open the **login page**; everything else needs a password.
Search engines are told not to list the site. Clock times always use German time.

**Logging in to PythonAnywhere:** always use **eu**.pythonanywhere.com. The account does not exist on www.pythonanywhere.com.

---

## Regular tasks

### Every 3 months: keep it running (important)

On the free plan the site stops unless someone confirms it every 3 months.
PythonAnywhere emails the account address before that happens.

> Log in at <https://eu.pythonanywhere.com> → **Web** tab → click **"Run until 3 months from today"**. That's all.

### Installing changes

Changes to the code are made on GitHub (e.g. with Claude Code). To put them live:

1. On PythonAnywhere open **Consoles** → **Bash** and run:

   ```bash
   cd ~/claude-dorm-portal && git pull
   ```

2. **Web** tab → **Reload**.

This only updates code files. The live data (`demo.sqlite3`) and the `backups` folder are never touched.

### Backups

- The portal copies its database into `claude-dorm-portal/backups/` automatically (at most every 6 hours, newest 10 kept).
- Once a month, also download `demo.sqlite3` in **Files** and keep it somewhere outside PythonAnywhere.
- **Restore:** upload a backup copy, rename it to `demo.sqlite3` (replacing the old one), then **Reload**.
  **Never replace `demo.sqlite3` otherwise**: that file holds all the live data.

### Usage numbers

Signed in as `leitung`, open **Passwörter & Zugang**. The **Nutzung** box shows anonymous monthly counts
(sign-ins, laundry starts, shelf items, bookings, notes). Use them to show whether the portal is used,
e.g. before asking for paid hosting.

### Passwords

Signed in as `leitung` → **Passwörter & Zugang**: change the residents' password (e.g. each semester),
the Dienstleiter password and your own. Changing a password signs out everyone who used it.

---

## Setting it up from scratch

Only needed if the portal has to be rebuilt (new account, other host). About 15 minutes, €0.

1. **Account:** <https://eu.pythonanywhere.com> → **Pricing & signup** → **Create a Beginner account** (free).
   Use a neutral username (it becomes the web address) and an email address the dorm keeps.
2. **Get the code:** **Consoles** → **Bash**:

   ```bash
   git clone https://github.com/studienheim-maria-dominika/wohnheim-portal.git claude-dorm-portal
   ```

3. **Web app:** **Web** tab → **Add a new web app** → **Next** → **Manual configuration** → newest **Python 3** → **Next**.
4. Click the link next to **WSGI configuration file**, delete everything in it and paste
   (replace `USERNAME` with the account's username):

   ```python
   import sys
   path = "/home/USERNAME/claude-dorm-portal"
   if path not in sys.path:
       sys.path.insert(0, path)
   from wsgi import application
   ```

   Click **Save**.
5. **Web** tab → **Force HTTPS** on → green **Reload** button.
6. **Passwords:** a fresh portal writes new passwords for `residents`, `dienstleiter` and `leitung` into the
   **server log** (Web tab → *Log files* → *server log*). Sign in at `/admin` as `leitung` and set your own
   under **Passwörter & Zugang**.
   To keep the old data instead, upload the saved `demo.sqlite3` into the `claude-dorm-portal` folder before step 5.
7. **Share:** open `/app` → **QR-Poster drucken**, and post the new `/app` link in the WhatsApp group.
   A new address means everyone has to re-add the app to their home screen.

## Limits of the free plan

- About 512 MB of storage. Photos are shrunk on upload, and taken shelf items are deleted after 15 minutes,
  so this lasts a long time. **Files** shows how much is used.
- The address stays `….eu.pythonanywhere.com`. A custom address such as `wohnheim.donboscoschwestern.de`
  needs a paid plan (or other hosting) later.

---

## Handover checklist (when the person running it moves out)

- [ ] PythonAnywhere login (eu.pythonanywhere.com, `mariadominika` + password) is given to the next person or the dorm management.
- [ ] GitHub login (account `studienheim-maria-dominika`) is handed over too.
- [ ] Both accounts use an email address the dorm keeps (the dorm Gmail).
- [ ] Passwords for `leitung`, `dienstleiter` and `residents` are handed over (or changed after the handover).
- [ ] The next person knows the "every 3 months" click.
- [ ] A recent copy of `demo.sqlite3` is saved outside PythonAnywhere.
