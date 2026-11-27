# Putting the portal online (PythonAnywhere, free)

This puts the portal on the internet at an address like `https://mariadominika.pythonanywhere.com`.
Everyone with the link can open the **login page**; everything else needs the password.
Search engines are told not to list the site.

Time needed: about 20 minutes. Cost: €0.

---

## 1. Create the account

1. Go to <https://www.pythonanywhere.com> → **Pricing & signup** → **Create a Beginner account** (free).
2. **Username:** choose a neutral name such as `mariadominika`. It becomes part of the web address
   and cannot be changed later, so do not use your own name.
3. **Email:** use an address the dorm keeps after you move out (e.g. a dorm or dorm-council address).
   PythonAnywhere sends its reminders there (see step 6).

## 2. Upload the portal

1. Make a zip of the `claude-dorm-portal` folder on your PC.
   - **For a clean start** (recommended): leave out `demo.sqlite3` and the `backups` folder.
     The portal then starts empty and creates new passwords (step 4).
   - **To keep the current data and passwords:** include `demo.sqlite3`.
2. On PythonAnywhere open **Files**, click **Upload a file** and upload the zip.
3. Open **Consoles** → **Bash** and type (replace the file name if yours is different):

   ```bash
   unzip claude-dorm-portal.zip
   ```

   You should now have the folder `/home/YOURNAME/claude-dorm-portal`.

## 3. Create the web app

1. Open the **Web** tab → **Add a new web app** → **Next**.
2. Choose **Manual configuration** (not Flask or Django) → pick the newest **Python 3** version → **Next**.
3. On the Web tab, click the link next to **WSGI configuration file**.
   Delete everything in that file and paste this (replace `YOURNAME` with your username):

   ```python
   import sys
   path = "/home/YOURNAME/claude-dorm-portal"
   if path not in sys.path:
       sys.path.insert(0, path)
   from wsgi import application
   ```

   Click **Save**.
4. Back on the **Web** tab, turn on **Force HTTPS**.
5. Click the big green **Reload** button.

## 4. First visit and passwords

Open `https://YOURNAME.pythonanywhere.com`.

- **Clean start:** the new passwords for `residents`, `dienstleiter` and `leitung` are written to the
  **server log** (Web tab → *Log files* → *server log*). Copy them somewhere safe, sign in as `leitung`
  and change them under **Passwörter & Zugang**.
- **Uploaded your database:** your existing passwords keep working.

## 5. Share it

1. Open `https://YOURNAME.pythonanywhere.com/app` (also linked at the bottom of every page and in the **Mehr** menu).
2. **QR-Poster drucken** prints an A4 poster with the QR code. Hang it by the mailboxes and in the laundry room.
3. Post the link in the WhatsApp group: `https://YOURNAME.pythonanywhere.com/app`.
   That page explains how to add the portal to the home screen on iPhone and Android.

---

## 6. Keep it running: every 3 months (important)

On the free plan the site stops unless someone confirms it every 3 months.
PythonAnywhere emails the account address before that happens.

> Log in → **Web** tab → click **"Run until 3 months from today"**. That's all.

## 7. Making changes later

The code lives on GitHub: <https://github.com/studienheim-maria-dominika/wohnheim-portal> (public; it contains
no passwords and no residents' data). Changes are made there (e.g. with Claude Code), then:

1. On PythonAnywhere open **Consoles** → **Bash** and run:

   ```bash
   cd ~/claude-dorm-portal && git pull
   ```

2. **Web** tab → **Reload**.

This only updates code files. The live data (`demo.sqlite3`) and the `backups` folder are never touched.

**One-time link to GitHub** (already done if `git pull` works). In the Bash console:

```bash
cd ~/claude-dorm-portal
git init -b main
git remote add origin https://github.com/studienheim-maria-dominika/wohnheim-portal.git
git fetch origin
git reset --hard origin/main
```

**Without GitHub:** upload the changed files in **Files** instead (replacing the old ones), then **Reload**.
**Never replace `demo.sqlite3` on the server**: that file holds all the live data.

## 8. Backups

- The portal copies its database into `claude-dorm-portal/backups/` automatically (at most every 6 hours, newest 10 kept).
- Once a month, also download `demo.sqlite3` in **Files** and keep it somewhere outside PythonAnywhere.
- **Restore:** upload a backup copy, rename it to `demo.sqlite3` (replacing the old one), then **Reload**.

## 9. Usage numbers

Signed in as `leitung`, open **Passwörter & Zugang**. The **Nutzung** box shows anonymous monthly counts
(sign-ins, laundry starts, shelf items, bookings, notes). Use them to show whether the portal is used,
e.g. before asking for paid hosting.

## 10. Limits of the free plan

- About 512 MB of storage. Photos are shrunk on upload, and taken shelf items are deleted after 15 minutes,
  so this lasts a long time. **Files** shows how much is used.
- The address stays `….pythonanywhere.com`. A custom address such as `wohnheim.donboscoschwestern.de`
  needs a paid plan (or other hosting) later.

---

## Handover checklist (when the person running it moves out)

- [ ] PythonAnywhere login (username + password) is given to the next person or the dorm management.
- [ ] GitHub login (account `studienheim-maria-dominika`) is handed over too.
- [ ] Account email (Account → Email) points to an address the dorm keeps.
- [ ] Passwords for `leitung`, `dienstleiter` and `residents` are handed over (or changed after the handover).
- [ ] The next person knows the "every 3 months" click (step 6).
- [ ] A recent copy of `demo.sqlite3` is saved outside PythonAnywhere.
