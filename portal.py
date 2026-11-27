"""Local Dormitory demo. Run with python3 portal.py (Python 3.9+)."""
import argparse
import base64
import gzip
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
import traceback
import webbrowser
from datetime import date, datetime, timedelta
from html import escape
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
BACKUPS = ROOT / "backups"
BEDDING_FEE = 10

# ---- Server settings. Change them with command-line options (python3 portal.py --help),
# ---- or with the PORTAL_* environment variables, not by editing this file.
PORT = int(os.environ.get("PORTAL_PORT", "8765"))
BIND = "127.0.0.1"          # "0.0.0.0" with --lan
LAN_MODE = False            # --lan: also accept requests addressed to this PC's local network address
EXTRA_HOSTS = {h.strip().lower() for h in os.environ.get("PORTAL_ALLOWED_HOSTS", "").split(",") if h.strip()}
SECURE_COOKIES = os.environ.get("PORTAL_SECURE_COOKIES") == "1"   # set when the site is served over https
TRUST_PROXY = os.environ.get("PORTAL_TRUST_PROXY") == "1"         # read the visitor address from X-Forwarded-For
ORIGIN = "http://127.0.0.1:%d" % PORT


def _split_host(value):
    value = (value or "").strip().lower()
    if value.startswith("["):
        name, _, rest = value[1:].partition("]")
        return name, rest[1:] if rest.startswith(":") else ""
    name, _, port = value.partition(":")
    return name, port


def host_allowed(value):
    """Accept only host names we expect. This blocks DNS-rebinding style attacks on the local server."""
    name, port = _split_host(value)
    if not name or port not in ("", str(PORT)):
        return False
    if name in ("localhost", "127.0.0.1", "::1") or name in EXTRA_HOSTS or value.strip().lower() in EXTRA_HOSTS:
        return True
    if not LAN_MODE:
        return False
    try:
        ip = ipaddress.ip_address(name)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return name.endswith(".local") or "." not in name


def origin_allowed(origin, host):
    parts = urlparse(origin or "")
    return parts.scheme in ("http", "https") and parts.netloc.lower() == (host or "").strip().lower()


# ---- Login throttling: too many wrong passwords from one address pauses logins from it.
LOGIN_WINDOW = 600
LOGIN_MAX_FAILS = 6
_login_fails = {}


def login_wait(ip):
    now = time.time()
    hits = [x for x in _login_fails.get(ip, []) if now - x < LOGIN_WINDOW]
    if hits:
        _login_fails[ip] = hits
    else:
        _login_fails.pop(ip, None)
    if len(hits) >= LOGIN_MAX_FAILS:
        return int(LOGIN_WINDOW - (now - hits[0])) + 1
    return 0


def login_failed(ip):
    if len(_login_fails) > 2000:
        _login_fails.clear()
    _login_fails.setdefault(ip, []).append(time.time())


def login_succeeded(ip):
    _login_fails.pop(ip, None)


def session_ttl(name):
    """Residents stay logged in on their phone for 30 days, Dienstleiter for 3, administrators for 8 hours."""
    if name == "residents":
        return 30 * 86400
    if name == "dienstleiter":
        return 3 * 86400
    return 8 * 3600


DB_LOCK = threading.RLock()
STATIC_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".txt": "text/plain; charset=utf-8",
}
STATIC_ALIASES = {
    "/manifest.webmanifest": "manifest.webmanifest",
    "/apple-touch-icon.png": "icons/apple-touch-icon.png",
    "/favicon.ico": "icons/favicon.png",
}
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self'; object-src 'none'; "
       "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

DEFAULT_TAGS = [
    ("kitchenware", "Kitchenware", "Küchensachen"),
    ("clothes", "Clothes", "Kleidung"),
    ("jewelry", "Jewelry & Accessories", "Schmuck & Accessoires"),
    ("bags", "Bags", "Taschen"),
    ("decor", "Room Decoration", "Zimmerdeko"),
    ("books", "Books", "Bücher"),
    ("crafts", "Arts & Crafts", "Basteln & Kunst"),
    ("knickknacks", "Knick-knacks", "Kleinkram"),
    ("stationery", "Stationery", "Schreibwaren"),
    ("other", "Other", "Sonstiges"),
]

DEFAULT_SETTINGS = {
    "mini_notes_hint": (
        "Everyone can post notes, vote (▲) on times or ideas, pin key info, and edit any note (e.g. add your name to Participants).",
        "Alle können Notizen schreiben, abstimmen (▲), wichtige Infos anpinnen und jede Notiz bearbeiten (z. B. eigenen Namen bei Teilnehmende ergänzen).",
    ),
    "calendar_hint": (
        "Click a free night to start, then click your check-out date (checkout is before 10:00 AM, so a check-out date can also be the next guest's check-in date).",
        "Klicke auf eine freie Nacht als Anreise und danach auf dein Abreisedatum (Check-out ist vor 10:00 Uhr, daher kann ein Abreisetag gleichzeitig der Anreisetag der nächsten Buchung sein).",
    ),
    "shelf_intro": (
        "Items currently on the 5th-floor shelf. Click an item for its description or to mark it as taken.",
        "Gegenstände im Tauschschrank (5. Stock). Klicke auf ein Foto für die Beschreibung oder um es als mitgenommen zu markieren.",
    ),
    "post_item_hint": (
        "Anonymous posting. Only include your name/room in the description if an item is too large for the 5th-floor shelf.",
        "Anonymes Einstellen. Gib Name/Zimmernummer nur in der Beschreibung an, falls etwas zu groß für den Schrank im 5. Stock ist.",
    ),
    "guest_rules": (
        "- Check-in is after 12:00 PM (noon); check-out is before 10:00 AM (so the check-out day is free for the next guest's check-in).\n"
        "- Please clean the room thoroughly before leaving.\n"
        "- If you use dormitory bed sheets/bedding, please strip the beds and place the used bedding in the designated bin in the basement (Keller).",
        "- Check-in ab 12:00 Uhr, Check-out vor 10:00 Uhr (damit der Abreisetag direkt wieder für neue Anreisen buchbar ist).\n"
        "- Bitte hinterlasse das Zimmer vor der Abreise sauber.\n"
        "- Falls Bettwäsche vom Wohnheim genutzt wird: Bitte Betten abziehen und die benutzte Bettwäsche in den vorgesehenen Behälter im Keller legen.",
    ),
    "booking_thanks": (
        "Your requested dates are now marked as pending on this room's calendar. "
        "Please check WhatsApp for confirmation from Kerstin. "
        "Once approved, please transfer the payment to: [Placeholder: Dormitory Bank / PayPal details here].",
        "Deine gewählten Nächte sind im Kalender dieses Zimmers jetzt als angefragt markiert. "
        "Bitte schau auf WhatsApp nach der Bestätigung durch Kerstin. "
        "Nach der Bestätigung überweise den Betrag bitte an: [Platzhalter: Bankverbindung / PayPal-Daten hier eintragen].",
    ),
    "duties_intro": (
        "Contact persons for our shared house duties.",
        "Ansprechpersonen für unsere gemeinsamen Hausdienste.",
    ),
    "shelf_max_days": ("60", "60"),
    "laundry_rules": (
        "- Enter your start time (defaults to now) and cycle duration to mark a machine as occupied.\n"
        "- If your laundry finishes early, please press 'Ended early' so others know it is free.\n"
        "- While a machine is running, one person can click 'Reserve next' to hold a grace window after the cycle ends.",
        "- Gib deine Startzeit (Standard: jetzt) und die Programmdauer ein, um eine Maschine als belegt zu markieren.\n"
        "- Wenn deine Wäsche früher fertig ist, klicke bitte auf ‚Früher beendet‘, damit andere Bescheid wissen.\n"
        "- Während eine Maschine läuft, kann eine Person ‚Als Nächste reservieren‘ klicken, um nach Programmende ein Zeitfenster zu reservieren.",
    ),
    "laundry_grace_mins": ("10", "10"),
    "house_rules_text": (
        "- Quiet hours are from 22:00 to 07:00.\n"
        "- Please keep shared kitchens, hallways, and the laundry room tidy.\n"
        "- Make sure the front entrance door is locked at night.",
        "- Nachtruhe gilt von 22:00 bis 07:00 Uhr.\n"
        "- Bitte Gemeinschaftsküchen, Flure und Waschküche sauber halten.\n"
        "- Die Hauseingangstür nachts bitte verschlossen halten.",
    ),
    "activities_intro": (
        "Choose an activity category to plan mini-activities, share notes, and track group expenses.",
        "Wähle eine Aktivitäts-Kategorie, um Mini-Aktivitäten zu planen, Notizen zu teilen und Gruppenausgaben zu verwalten.",
    ),
    "activity_gallery_hint": (
        "Share photos of baked goods, crafts, or event setups (please only upload photos without faces until confirmed with administration).",
        "Teilt hier Fotos von Gebäck, Bastelarbeiten oder Dekoration (bitte vorerst nur Fotos ohne Gesichter hochladen).",
    ),
}

ADMIN_ONLY_SETTINGS = {"mini_notes_hint", "calendar_hint", "guest_rules", "booking_thanks", "house_rules_text"}
GENERAL_SETTINGS = set(DEFAULT_SETTINGS.keys()) - ADMIN_ONLY_SETTINGS

PLACEHOLDER_ROOM_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 500">
  <rect width="800" height="500" fill="#fff4e0"/>
  <rect x="60" y="90" width="680" height="320" rx="24" fill="#ff9f00" opacity="0.22"/>
  <rect x="140" y="250" width="520" height="90" rx="14" fill="#ff9f00"/>
  <rect x="170" y="200" width="180" height="60" rx="12" fill="#ffffff" stroke="#000000" stroke-width="4"/>
  <rect x="370" y="200" width="180" height="60" rx="12" fill="#ffffff" stroke="#000000" stroke-width="4"/>
  <rect x="140" y="325" width="24" height="45" fill="#000000"/>
  <rect x="636" y="325" width="24" height="45" fill="#000000"/>
</svg>""".encode("utf-8")

db = sqlite3.connect(ROOT / "demo.sqlite3", check_same_thread=False, timeout=15)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS accounts (name TEXT PRIMARY KEY, password TEXT);
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, name TEXT NOT NULL, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    title TEXT,
    image BLOB,
    description TEXT DEFAULT '',
    tags TEXT NOT NULL,
    created_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY,
    floor TEXT NOT NULL,
    max_guests INTEGER NOT NULL,
    price REAL NOT NULL,
    image BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY,
    room_id INTEGER DEFAULT 1,
    name TEXT,
    arrival TEXT,
    departure TEXT,
    guests INTEGER,
    bedding INTEGER DEFAULT 0,
    total_price REAL DEFAULT 0,
    notes TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    approved_at INTEGER DEFAULT 0,
    paid INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS duties (id INTEGER PRIMARY KEY, duty TEXT, contact TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, en TEXT DEFAULT '', de TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tags (key TEXT PRIMARY KEY, en TEXT NOT NULL, de TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS machines (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'washer',
    status TEXT NOT NULL DEFAULT 'free',
    start_ts INTEGER DEFAULT 0,
    end_ts INTEGER DEFAULT 0,
    reserved INTEGER DEFAULT 0,
    pending_until INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    pinned INTEGER DEFAULT 0,
    created_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS documents (
    key TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    data BLOB NOT NULL,
    updated_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    image BLOB NOT NULL,
    created_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS activity_photos (
    id INTEGER PRIMARY KEY,
    activity_id INTEGER NOT NULL,
    image BLOB NOT NULL,
    caption TEXT DEFAULT '',
    created_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mini_activities (
    id INTEGER PRIMARY KEY,
    activity_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    max_participants INTEGER NOT NULL DEFAULT 10,
    image BLOB NOT NULL,
    contribution REAL DEFAULT 0,
    starting_balance REAL DEFAULT 0,
    created_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mini_notes (
    id INTEGER PRIMARY KEY,
    mini_id INTEGER NOT NULL,
    title TEXT DEFAULT '',
    body TEXT NOT NULL,
    pinned INTEGER DEFAULT 0,
    votes INTEGER DEFAULT 0,
    created_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mini_members (
    id INTEGER PRIMARY KEY,
    mini_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    paid INTEGER DEFAULT 0,
    paid_date TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mini_logs (
    id INTEGER PRIMARY KEY,
    mini_id INTEGER NOT NULL,
    log_date TEXT NOT NULL,
    person TEXT NOT NULL,
    took_out REAL DEFAULT 0,
    put_back REAL DEFAULT 0,
    items_json TEXT DEFAULT '[]',
    created_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mini_log_photos (
    id INTEGER PRIMARY KEY,
    log_id INTEGER NOT NULL,
    image BLOB NOT NULL
);
""")

# Safely upgrade existing demo.sqlite3 without resetting passwords, items, or bookings
item_cols = {r[1] for r in db.execute("PRAGMA table_info(items)")}
booking_cols = {r[1] for r in db.execute("PRAGMA table_info(bookings)")}
with db:
    if "votes" not in {r[1] for r in db.execute("PRAGMA table_info(announcements)")}:
        db.execute("ALTER TABLE announcements ADD COLUMN votes INTEGER NOT NULL DEFAULT 0")
    if "description" not in item_cols:
        db.execute("ALTER TABLE items ADD COLUMN description TEXT DEFAULT ''")
    if "thumb" not in item_cols:
        db.execute("ALTER TABLE items ADD COLUMN thumb BLOB")
    if "taken_at" not in item_cols:
        db.execute("ALTER TABLE items ADD COLUMN taken_at INTEGER NOT NULL DEFAULT 0")
    if "tags" not in item_cols:
        db.execute("ALTER TABLE items ADD COLUMN tags TEXT DEFAULT 'other'")
    if "created_at" not in item_cols:
        db.execute("ALTER TABLE items ADD COLUMN created_at INTEGER DEFAULT 0")
    db.execute("UPDATE items SET created_at = ? WHERE created_at IS NULL OR created_at = 0", (int(time.time()),))

    if "room_id" not in booking_cols:
        db.execute("ALTER TABLE bookings ADD COLUMN room_id INTEGER DEFAULT 1")
    if "bedding" not in booking_cols:
        db.execute("ALTER TABLE bookings ADD COLUMN bedding INTEGER DEFAULT 0")
    if "total_price" not in booking_cols:
        db.execute("ALTER TABLE bookings ADD COLUMN total_price REAL DEFAULT 0")
    if "notes" not in booking_cols:
        db.execute("ALTER TABLE bookings ADD COLUMN notes TEXT DEFAULT ''")
    if "status" not in booking_cols:
        db.execute("ALTER TABLE bookings ADD COLUMN status TEXT DEFAULT 'pending'")
    if "paid" not in booking_cols:
        db.execute("ALTER TABLE bookings ADD COLUMN paid INTEGER NOT NULL DEFAULT 0")
    if "approved_at" not in booking_cols:
        db.execute("ALTER TABLE bookings ADD COLUMN approved_at INTEGER DEFAULT 0")
        db.execute("UPDATE bookings SET approved_at = id WHERE status = 'approved' AND (approved_at IS NULL OR approved_at = 0)")
    for k, (en_v, de_v) in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO settings(key, en, de) VALUES (?,?,?)", (k, en_v, de_v))

    if not db.execute("SELECT 1 FROM tags").fetchone():
        for k, en_v, de_v in DEFAULT_TAGS:
            db.execute("INSERT INTO tags(key, en, de) VALUES (?,?,?)", (k, en_v, de_v))

    if not db.execute("SELECT 1 FROM rooms").fetchone():
        db.execute(
            "INSERT INTO rooms(floor, max_guests, price, image) VALUES (?,?,?,?)",
            ("Guest room in 3rd floor / Gästezimmer 3. Stock", 2, 15, b""),
        )
        db.execute(
            "INSERT INTO rooms(floor, max_guests, price, image) VALUES (?,?,?,?)",
            ("Guest room in 5th floor / Gästezimmer 5. Stock", 3, 20, b""),
        )

    if not db.execute("SELECT 1 FROM machines").fetchone():
        db.execute("INSERT INTO machines(name, kind) VALUES (?,?)", ("Machine 1", "dryer"))
        db.execute("INSERT INTO machines(name, kind) VALUES (?,?)", ("Machine 2", "washer"))
        db.execute("INSERT INTO machines(name, kind) VALUES (?,?)", ("Machine 3", "washer"))


def digest(value, salt):
    return hashlib.scrypt(value.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()


def setpass(name, value):
    salt = secrets.token_hex(16)
    db.execute("INSERT OR REPLACE INTO accounts VALUES (?,?)", (name, salt + "." + digest(value, salt)))


def valid(name, value):
    row = db.execute("SELECT password FROM accounts WHERE name=?", (name,)).fetchone()
    if not row:
        return False
    salt, key = row[0].split(".")
    return hmac.compare_digest(key, digest(value, salt))


had_any_accounts = bool(db.execute("SELECT 1 FROM accounts").fetchone())
with db:
    for account in ("residents", "dienstleiter", "leitung"):
        if not db.execute("SELECT 1 FROM accounts WHERE name=?", (account,)).fetchone():
            pw = secrets.token_urlsafe(10)
            setpass(account, pw)
            print(account, "password:", pw, flush=True)
    if not had_any_accounts and not db.execute("SELECT 1 FROM duties").fetchone():
        db.execute(
            "INSERT INTO duties(duty,contact) VALUES (?,?)",
            ("Plant Care / Pflanzendienst", "Mia (Example) · Note in foyer mailbox"),
        )

class SessionStore:
    """Login sessions live in the database (token stored hashed), so restarting the server keeps people logged in."""

    @staticmethod
    def _key(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def get(self, token, default=("", 0)):
        if not token:
            return default
        row = db.execute("SELECT name, expires FROM sessions WHERE token=?", (self._key(token),)).fetchone()
        return (row["name"], row["expires"]) if row else default

    def __setitem__(self, token, value):
        name, until = value
        with db:
            db.execute("INSERT OR REPLACE INTO sessions(token, name, expires) VALUES (?,?,?)", (self._key(token), name, int(until)))

    def pop(self, token, default=None):
        with db:
            db.execute("DELETE FROM sessions WHERE token=?", (self._key(token),))
        return default

    def revoke_account(self, name):
        with db:
            db.execute("DELETE FROM sessions WHERE name=?", (name,))

    def purge(self):
        with db:
            db.execute("DELETE FROM sessions WHERE expires < ?", (int(time.time()),))


sessions = SessionStore()
sessions.purge()
with db:
    db.execute("DELETE FROM items WHERE taken_at > 0 AND taken_at < ?", (int(time.time()) - 7 * 86400,))
session_votes = {}

TEXT = {
    "en": {
        "brand": "Dormitory",
        "kicker": "DORMITORY PORTAL &nbsp; / &nbsp; LOCAL DEMO",
        "footer": "Dormitory · Preview version with sample data",
        "welcome": "Welcome home.",
        "please_login": "Please log in.",
        "admin": "Administration",
        "dienstleiter": "Dienstleiter",
        "residents": "Residents",
        "resident_access": "Resident Access",
        "dienstleiter_access": "Dienstleiter Access",
        "admin_account": "Admin account",
        "password": "Password",
        "login": "Log in",
        "logout": "Log out",
        "overview": "Overview",
        "shelf": "Exchange Shelf",
        "guest_room": "Guest Room",
        "house_duties": "House Duties",
        "admin_settings": "Passwords & Access",
        "card_admin_desc": "",
        "our_house": "Our House.",
        "card_shelf_desc": "",
        "card_guest_desc": "",
        "card_duties_desc": "",
        # Editable notices
        "edit_msg_btn": "Edit message",
        "msg_en_label": "Message in English",
        "msg_de_label": "Message in German",
        "msg_bullet_hint": "Tip: Start lines with '- ' to format them as bullet points.",
        # Exchange Shelf
        "filter_btn": "Filter by tag",
        "filter_active": "Filter active",
        "apply_filter": "Apply filter",
        "show_all": "Show all",
        "no_items": "No items are currently on the exchange shelf.",
        "no_filtered_items": "No items match the selected tags.",
        "post_item": "Add new item",
        "photo_label": "Upload photo * (object only, no faces)",
        "item_title": "Add name *",
        "item_desc": "Add description (optional, max. 100 words)",
        "item_desc_ph": "Condition, size, color, or room number if too large for the shelf...",
        "select_tags": "Add tag * (choose from menu)",
        "choose_tags_menu": "Choose from menu...",
        "publish": "Post",
        "back_shelf": "← Back to Exchange Shelf",
        "posted_today": "Placed on shelf today",
        "posted_days": "On shelf for {days} day(s)",
        "overdue_badge": "{days}+ days on shelf",
        "taken_btn": "Taken (I picked it up)",
        "taken_confirm": "Did you physically take this item from the 5th-floor shelf? This will remove the listing; you can undo it for 7 days.",
        "remove": "Remove",
        "manage_shelf_admin": "Manage tags & alert threshold",
        "shelf_days_label": "Highlight items after how many days on shelf?",
        "add_tag_title": "Add new tag",
        "tag_en_label": "Tag name (English) *",
        "tag_de_label": "Tag name (German) *",
        "add_tag_btn": "Add tag",
        "existing_tags_title": "Current tags",
        "remove_tag_confirm": "Delete this tag from the menu?",
        # Guest Room
        "guest_rules_title": "Guest Room Rules & Information",
        "no_rooms": "No guest rooms are currently listed.",
        "send_booking_btn": "Send booking request",
        "book_room_admin_btn": "Book / View calendar",
        "back_guest_rooms": "← Back to Guest Rooms",
        "admin_auto_approve_note": "Admin mode: Bookings you submit here are automatically approved and marked as occupied on the calendar.",
        "legend_free": "Free",
        "legend_pending": "Pending",
        "legend_occupied": "Occupied",
        "checkin_label": "Check-in date *",
        "checkout_label": "Check-out date *",
        "nights_selected": "Selected stay",
        "night_singular": "night",
        "night_plural": "nights",
        "guests_count": "Number of guests *",
        "bedding_option": "Request bed sheets / bedding (+10 €)",
        "sum_to_pay": "Sum to pay",
        "resident_name_room": "Your name & room number *",
        "resident_name_ph": "e.g. Anna M., Room 412",
        "admin_name_ph": "e.g. Anna M., Room 412 or Administration",
        "special_requests": "Special requests (optional, max. 100 words)",
        "special_requests_ph": "Arrival time, extra pillow, questions for Kerstin...",
        "send_request_submit": "Send request",
        "book_directly_btn": "Save as occupied",
        "request_recorded": "Booking request sent.",
        "admin_requests_drawer": "View requests",
        "admin_approved_drawer": "Approved bookings",
        "booking_unpaid": "Unpaid",
        "admin_add_room_drawer": "Add guest room",
        "admin_thanks_msg_drawer": "Confirmation & payment message",
        "status_pending": "Pending",
        "status_approved": "Occupied (Approved)",
        "approve_btn": "Approve",
        "reject_btn": "Reject",
        "free_dates_btn": "Cancel & free dates",
        "cancel_booking_confirm": "Cancel this approved booking and free its dates on the calendar?",
        "bedding_yes": "Bedding (+10 €)",
        "bedding_no": "Own bedding",
        "no_requests": "No pending guest room requests.",
        "no_approved": "No approved bookings currently saved.",
        "sort_label": "Sort by:",
        "sort_soonest": "Soonest date",
        "sort_latest_approved": "Latest approved",
        "edit_room_btn": "Edit room",
        "replace_photo_label": "Replace photo (optional)",
        "save_changes": "Save changes",
        "admin_rooms_hint": "Add a photo, a room name, the guest limit and the nightly rate.",
        "room_floor_label": "Floor / Room location *",
        "room_floor_ph": "e.g. Guest room in 3rd floor",
        "room_max_label": "Max guests *",
        "room_price_label": "Price per night (€) *",
        "room_photo_label": "Room photo *",
        "room_preview_label": "Listing preview",
        "add_room_btn": "Add guest room",
        "remove_room_confirm": "Remove this guest room and all its bookings from the portal?",
        # Washing Machines
        "laundry": "Washing Machines",
        "card_laundry_desc": "",
        "laundry_rules_title": "Laundry Room · How It Works",
        "no_machines": "No machines are currently listed.",
        "machine_washer": "Washing Machine",
        "machine_dryer": "Dryer",
        "status_free": "Free",
        "status_machine_occ": "Occupied",
        "status_machine_pend": "Pending (Reserved)",
        "free_at_time": "Free at {time}",
        "pending_until_time": "Reserved until {time}",
        "time_left": "Time left",
        "start_time_label": "Start time *",
        "duration_label": "Cycle duration *",
        "hours_short": "h",
        "mins_short": "min",
        "ends_at_preview": "Free again at",
        "start_machine_btn": "Start / Block machine",
        "ended_early_btn": "Ended early",
        "reserve_next_btn": "Reserve next",
        "reserved_badge": "Next slot reserved",
        "cancel_reserve_btn": "Cancel reservation",
        "pending_hint": "Reserved for the next person. Enter your start time and duration to claim it, otherwise it resets to free.",
        "manage_laundry_admin": "Manage machines & grace period",
        "grace_mins_label": "Reservation grace period after cycle ends (minutes)",
        "add_machine_title": "Add machine",
        "machine_name_label": "Machine name / number *",
        "machine_name_ph": "e.g. Machine 4",
        "machine_kind_label": "Machine type *",
        "add_machine_btn": "Add machine",
        "edit_machine_btn": "Edit machine",
        "remove_machine_confirm": "Remove this machine from the portal?",
        # House Rules & Notifs
        "rules_nav": "Rules & News",
        "rules_card_title": "Announcements & House Rules",
        "card_rules_desc": "",
        "announcements_heading": "Announcements",
        "no_announcements": "No announcements at the moment.",
        "pinned_badge": "Important",
        "pin_btn": "Pin as important",
        "unpin_btn": "Unpin",
        "add_announcement_title": "Add announcement",
        "ann_title_label": "Headline *",
        "ann_body_label": "Message *",
        "ann_pin_label": "Pin to top / Mark as important",
        "publish_ann_btn": "Post announcement",
        "remove_ann_confirm": "Delete this announcement?",
        "house_rules_drawer": "House Rules (click to expand)",
        "open_rules_pdf": "Open House Rules (PDF)",
        "manage_rules_pdf": "House Rules PDF (Admin)",
        "pdf_upload_label": "Upload or replace PDF (max. 8 MB)",
        "upload_pdf_btn": "Save PDF",
        "remove_pdf_btn": "Remove PDF",
        "pdf_current": "Current file",
        "pdf_invalid": "Please select a valid PDF file under 8 MB.",
        # Activities & Mini-Activities
        "activities_nav": "Activities",
        "activities_title": "Activities",
        "card_activities_desc": "",
        "no_activities": "No activity boards yet. Create the first category below!",
        "add_activity_drawer": "Add new activity category",
        "act_photo_label": "Cover photo * (generic photo)",
        "act_name_label": "Activity / Category name *",
        "act_name_ph": "e.g. Baking & Cooking, Movie Nights, Arts & Crafts",
        "add_activity_btn": "Create activity",
        "open_activity_btn": "Open board",
        "delete_activity_btn": "Delete activity",
        "remove_activity_confirm": "Delete this entire activity category and all its mini-activities, photos, notes, and budget logs?",
        "back_activities": "← Back to Activities",
        "mini_activities_heading": "Mini-Activities",
        "no_minis": "No mini-activities created yet. Add one below!",
        "create_mini_drawer": "Create mini-activity",
        "mini_photo_label": "Photo *",
        "mini_name_label": "Mini-activity name *",
        "mini_name_ph": "e.g. Christmas Cookies Baking",
        "mini_max_label": "Max number of participants *",
        "create_mini_btn": "Create mini-activity",
        "max_participants_badge": "Max {max} participants",
        "open_mini_btn": "Open mini-activity",
        "delete_mini_btn": "Delete mini-activity",
        "remove_mini_confirm": "Delete this mini-activity along with all its notes and budget tracker entries?",
        "act_gallery_heading": "Activity Photo Gallery",
        "no_act_photos": "No photos uploaded to this activity gallery yet.",
        "add_gallery_photo_drawer": "Add photo to gallery",
        "gal_photo_label": "Select photo *",
        "gal_caption_label": "Short caption (optional)",
        "gal_caption_ph": "e.g. Cinnamon stars batch #1",
        "upload_gal_btn": "Upload photo",
        "remove_photo_confirm": "Delete this photo from the gallery?",
        # Mini-Activity Workspace (Notes + Budget Tracker)
        "back_activity": "← Back to {name}",
        "starter_note_title": "Participants / Teilnehmende",
        "starter_note_body": "Participants: ",
        "mini_notes_heading": "Shared Notes & Coordination",
        "no_mini_notes": "No notes posted yet.",
        "add_note_drawer": "Post a new note",
        "note_title_label": "Topic / Title (optional)",
        "note_title_ph": "e.g. Meeting time, Ingredients to bring, Agreed contribution",
        "note_body_label": "Note *",
        "note_body_ph": "Write details, times, ingredients, or participant names...",
        "note_pin_label": "Pin note to top",
        "post_note_btn": "Post note",
        "edit_note_btn": "Edit",
        "upvote_title": "Vote for this note",
        "remove_note_confirm": "Delete this note?",
        # Mini-Activity Budget Tracker
        "tracker_heading": "Shared Budget & Expense Tracker",
        "tracker_balance_title": "Current Cash Box Balance",
        "tracker_contrib_lbl": "Contributions",
        "tracker_paid_count": "{paid}/{total} paid ({rate} each)",
        "tracker_net_spent_lbl": "Net Spent",
        "tracker_net_sub": "Took out − Put back",
        "tracker_carry_lbl": "Carried Over",
        "tracker_carry_sub": "Starting balance",
        "tracker_settings_drawer": "Set agreed contribution & starting balance",
        "tracker_rate_label": "Contribution per person (€)",
        "tracker_start_label": "Carried-over starting balance (€)",
        "tracker_save_settings": "Save budget settings",
        "tracker_members_heading": "Participant Contributions",
        "tracker_members_hint": "Click a person's card to toggle Paid / Unpaid.",
        "tracker_paid_badge": "✓ Paid",
        "tracker_unpaid_badge": "+ {rate}",
        "tracker_paid_on": "Paid {rate} ({date})",
        "tracker_click_pay": "Click to mark {rate} paid",
        "tracker_add_member_ph": "Add participant name...",
        "tracker_add_member_btn": "+ Add Person",
        "tracker_edit_members_btn": "Edit / Remove Names",
        "tracker_log_drawer": "Log Expense & Receipt",
        "tracker_log_date": "Date (DD/MM/YYYY) *",
        "tracker_log_person": "Participant name *",
        "tracker_log_person_ph": "e.g. Nesila",
        "tracker_took_out": "Took Out (€)",
        "tracker_put_back": "Put Back / Change (€)",
        "tracker_fund_change": "Fund change",
        "tracker_items_total": "Itemized total",
        "tracker_items_heading": "Expenses Breakdown (optional)",
        "tracker_add_item_btn": "+ Add Item",
        "tracker_item_ph": "Item (e.g. Flour, butter, sugar)",
        "tracker_receipts_label": "Receipt photo(s) (up to 4)",
        "tracker_save_log_btn": "Save Entry & Update Balance",
        "tracker_logs_heading": "Expense & Receipt Logs",
        "tracker_no_logs": "No expenses logged yet.",
        "tracker_log_summary": "took out {out}, put back {back}",
        "tracker_no_items_listed": "No itemized breakdown listed",
        "tracker_expenses_prefix": "Expenses",
        "tracker_view_receipt": "View",
        "tracker_delete_log_confirm": "Delete this expense entry? The balance will automatically recalculate.",
        "close_modal": "Close ✕",
        # House Duties & Passwords
        "remove_entry": "Remove entry",
        "no_duties": "No house duties are currently listed.",
        "add_duty": "Add a duty",
        "duty_label": "Duty",
        "contact_label": "Contact person and how to reach them",
        "save": "Save",
        "to": "to",
        "guests": "Guests",
        "change_resident_pw": "Change Resident Password",
        "change_dienstleiter_pw": "Change Dienstleiter Password",
        "new_shared_pw": "New shared password",
        "change_logout_residents": "Change and log out residents",
        "change_logout_dienstleiter": "Change and log out Dienstleiter",
        "your_pw": "Your Password",
        "current_pw": "Current password",
        "new_pw": "New password",
        "change_pw": "Change password",
        "create_admin": "Create Admin Account",
        "account_name": "Account name",
        "initial_pw": "Initial password",
        "create_account": "Create account",
        "revoke_access": "Revoke access",
        "page_unavailable": "Page not available.",
        "photo_too_large": "Photo too large.",
        "login_failed": "Login failed.",
        "back_residents": "Back to Resident Access",
        "back_dienstleiter": "Back to Dienstleiter",
        "go_admin": "Go to Administration",
        "check_input": "Check your input.",
        "check_input_body": "Please make sure all required fields are filled, selected dates are free, and text fields stay within limits.",
        "back_overview": "Back to Overview",
    },
    "de": {
        "brand": "Wohnheim",
        "kicker": "WOHNHEIMPORTAL &nbsp; / &nbsp; LOKALE DEMO",
        "footer": "Wohnheim · Vorführversion mit Beispieldaten",
        "welcome": "Willkommen im Haus.",
        "please_login": "Bitte anmelden.",
        "admin": "Verwaltung",
        "dienstleiter": "Dienstleiter",
        "residents": "Bewohnerinnen",
        "resident_access": "Zugang für Bewohnerinnen",
        "dienstleiter_access": "Zugang für Dienstleiter",
        "admin_account": "Administrationskonto",
        "password": "Passwort",
        "login": "Anmelden",
        "logout": "Abmelden",
        "overview": "Übersicht",
        "shelf": "Tauschschrank",
        "guest_room": "Gästezimmer",
        "house_duties": "Hausdienste",
        "admin_settings": "Passwörter & Zugang",
        "card_admin_desc": "",
        "our_house": "Unser Haus.",
        "card_shelf_desc": "",
        "card_guest_desc": "",
        "card_duties_desc": "",
        # Editable notices
        "edit_msg_btn": "Text bearbeiten",
        "msg_en_label": "Nachricht auf Englisch",
        "msg_de_label": "Nachricht auf Deutsch",
        "msg_bullet_hint": "Tipp: Zeilen mit '- ' beginnen, um Aufzählungspunkte zu erstellen.",
        # Exchange Shelf
        "filter_btn": "Nach Etiketten filtern",
        "filter_active": "Filter aktiv",
        "apply_filter": "Filter anwenden",
        "show_all": "Alle anzeigen",
        "no_items": "Derzeit stehen keine Gegenstände im Tauschschrank.",
        "no_filtered_items": "Keine Gegenstände passen zu den gewählten Etiketten.",
        "post_item": "Neuen Gegenstand hinzufügen",
        "photo_label": "Foto hochladen * (nur Gegenstand, keine Gesichter)",
        "item_title": "Name hinzufügen *",
        "item_desc": "Beschreibung hinzufügen (optional, max. 100 Wörter)",
        "item_desc_ph": "Zustand, Größe, Farbe oder Zimmernummer bei großen Gegenständen...",
        "select_tags": "Etikett hinzufügen * (aus Menü wählen)",
        "choose_tags_menu": "Aus Menü wählen...",
        "publish": "Veröffentlichen",
        "back_shelf": "← Zurück zum Tauschschrank",
        "posted_today": "Heute eingestellt",
        "posted_days": "Seit {days} Tag(en) im Schrank",
        "overdue_badge": "Über {days} Tage im Schrank",
        "taken_btn": "Mitgenommen (Eintrag entfernen)",
        "taken_confirm": "Hast du diesen Gegenstand physisch aus dem Tauschschrank im 5. Stock mitgenommen? Der Eintrag wird entfernt (7 Tage lang rückgängig zu machen).",
        "remove": "Entfernen",
        "manage_shelf_admin": "Etiketten & Zeitlimit verwalten",
        "shelf_days_label": "Gegenstände markieren ab wie vielen Tagen im Schrank?",
        "add_tag_title": "Neues Etikett hinzufügen",
        "tag_en_label": "Etikett-Name (Englisch) *",
        "tag_de_label": "Etikett-Name (Deutsch) *",
        "add_tag_btn": "Etikett hinzufügen",
        "existing_tags_title": "Vorhandene Etiketten",
        "remove_tag_confirm": "Dieses Etikett wirklich aus dem Menü löschen?",
        # Guest Room
        "guest_rules_title": "Gästezimmer · Regeln & Hinweise",
        "no_rooms": "Derzeit sind keine Gästezimmer eingetragen.",
        "send_booking_btn": "Buchungsanfrage senden",
        "book_room_admin_btn": "Buchen / Kalender ansehen",
        "back_guest_rooms": "← Zurück zu den Gästezimmern",
        "admin_auto_approve_note": "Verwaltungsmodus: Deine Buchung wird sofort bestätigt und direkt als belegt eingetragen.",
        "legend_free": "Frei",
        "legend_pending": "Angefragt",
        "legend_occupied": "Belegt",
        "checkin_label": "Anreisedatum *",
        "checkout_label": "Abreisedatum *",
        "nights_selected": "Gewählter Aufenthalt",
        "night_singular": "Nacht",
        "night_plural": "Nächte",
        "guests_count": "Anzahl der Gäste *",
        "bedding_option": "Bettwäsche anfordern (+10 €)",
        "sum_to_pay": "Gesamtbetrag",
        "resident_name_room": "Dein Name & Zimmernummer *",
        "resident_name_ph": "z. B. Anna M., Zimmer 412",
        "admin_name_ph": "z. B. Anna M., Zimmer 412 oder Verwaltung",
        "special_requests": "Besondere Wünsche (optional, max. 100 Wörter)",
        "special_requests_ph": "Ankunftszeit, zusätzliches Kissen, Fragen an Kerstin...",
        "send_request_submit": "Anfrage absenden",
        "book_directly_btn": "Als belegt speichern",
        "request_recorded": "Buchungsanfrage gesendet.",
        "admin_requests_drawer": "Anfragen ansehen",
        "admin_approved_drawer": "Bestätigte Buchungen",
        "booking_unpaid": "Offen",
        "admin_add_room_drawer": "Gästezimmer hinzufügen",
        "admin_thanks_msg_drawer": "Bestätigungs- & Zahlungstext",
        "status_pending": "Angefragt",
        "status_approved": "Belegt (Bestätigt)",
        "approve_btn": "Bestätigen",
        "reject_btn": "Ablehnen",
        "free_dates_btn": "Stornieren & freigeben",
        "cancel_booking_confirm": "Diese bestätigte Buchung stornieren und die Nächte im Kalender wieder freigeben?",
        "bedding_yes": "Mit Bettwäsche (+10 €)",
        "bedding_no": "Eigene Bettwäsche",
        "no_requests": "Keine offenen Gästezimmer-Anfragen.",
        "no_approved": "Derzeit keine bestätigten Buchungen gespeichert.",
        "sort_label": "Sortieren nach:",
        "sort_soonest": "Nächstes Datum",
        "sort_latest_approved": "Zuletzt bestätigt",
        "edit_room_btn": "Zimmer bearbeiten",
        "replace_photo_label": "Neues Foto (optional)",
        "save_changes": "Änderungen speichern",
        "admin_rooms_hint": "Bitte Foto, Zimmername, maximale Gästezahl und Preis pro Nacht angeben.",
        "room_floor_label": "Etage / Zimmerbezeichnung *",
        "room_floor_ph": "z. B. Gästezimmer im 3. Stock",
        "room_max_label": "Max. Gäste *",
        "room_price_label": "Preis pro Nacht (€) *",
        "room_photo_label": "Foto vom Zimmer *",
        "room_preview_label": "Vorschau der Anzeige",
        "add_room_btn": "Gästezimmer hinzufügen",
        "remove_room_confirm": "Dieses Gästezimmer samt Buchungen wirklich entfernen?",
        # Washing Machines
        "laundry": "Wasch­maschinen",
        "card_laundry_desc": "",
        "laundry_rules_title": "Waschküche · So funktioniert's",
        "no_machines": "Derzeit sind keine Maschinen eingetragen.",
        "machine_washer": "Waschmaschine",
        "machine_dryer": "Trockner",
        "status_free": "Frei",
        "status_machine_occ": "Belegt",
        "status_machine_pend": "Reserviert (Wartefenster)",
        "free_at_time": "Wieder frei um {time} Uhr",
        "pending_until_time": "Reserviert bis {time} Uhr",
        "time_left": "Verbleibend",
        "start_time_label": "Startzeit *",
        "duration_label": "Programmdauer *",
        "hours_short": "Std.",
        "mins_short": "Min.",
        "ends_at_preview": "Wieder frei um",
        "start_machine_btn": "Maschine starten / belegen",
        "ended_early_btn": "Früher beendet",
        "reserve_next_btn": "Als Nächste reservieren",
        "reserved_badge": "Nächster Durchgang reserviert",
        "cancel_reserve_btn": "Reservierung aufheben",
        "pending_hint": "Für die nächste Person reserviert. Startzeit und Dauer eingeben zum Belegen, sonst wird sie wieder frei.",
        "manage_laundry_admin": "Maschinen & Reservierungszeit verwalten",
        "grace_mins_label": "Reservierungs-Zeitfenster nach Programmende (Minuten)",
        "add_machine_title": "Maschine hinzufügen",
        "machine_name_label": "Maschinenname / Nummer *",
        "machine_name_ph": "z. B. Maschine 4",
        "machine_kind_label": "Gerätetyp *",
        "add_machine_btn": "Maschine hinzufügen",
        "edit_machine_btn": "Maschine bearbeiten",
        "remove_machine_confirm": "Diese Maschine wirklich entfernen?",
        # House Rules & Notifs
        "rules_nav": "Infos & Regeln",
        "rules_card_title": "Ankün­digungen & Haus­ordnung",
        "card_rules_desc": "",
        "announcements_heading": "Ankündigungen",
        "no_announcements": "Derzeit gibt es keine Ankündigungen.",
        "pinned_badge": "Wichtig",
        "pin_btn": "Als wichtig anpinnen",
        "unpin_btn": "Nicht mehr anpinnen",
        "add_announcement_title": "Ankündigung hinzufügen",
        "ann_title_label": "Überschrift *",
        "ann_body_label": "Nachricht *",
        "ann_pin_label": "Oben anpinnen / Als wichtig markieren",
        "publish_ann_btn": "Ankündigung veröffentlichen",
        "remove_ann_confirm": "Diese Ankündigung wirklich löschen?",
        "house_rules_drawer": "Hausordnung (zum Aufklappen klicken)",
        "open_rules_pdf": "Hausordnung öffnen (PDF)",
        "manage_rules_pdf": "Hausordnung-PDF (Verwaltung)",
        "pdf_upload_label": "PDF hochladen oder ersetzen (max. 8 MB)",
        "upload_pdf_btn": "PDF speichern",
        "remove_pdf_btn": "PDF entfernen",
        "pdf_current": "Aktuelle Datei",
        "pdf_invalid": "Bitte wähle eine gültige PDF-Datei unter 8 MB.",
        # Activities & Mini-Activities
        "activities_nav": "Aktivitäten",
        "activities_title": "Aktivitäten",
        "card_activities_desc": "",
        "no_activities": "Noch keine Aktivitäten vorhanden. Erstelle unten die erste Kategorie!",
        "add_activity_drawer": "Neue Aktivitäts-Kategorie hinzufügen",
        "act_photo_label": "Titelbild * (allgemeines Foto)",
        "act_name_label": "Aktivität / Kategoriename *",
        "act_name_ph": "z. B. Backen & Kochen, Filmabende, Basteln",
        "add_activity_btn": "Aktivität erstellen",
        "open_activity_btn": "Board öffnen",
        "delete_activity_btn": "Aktivität löschen",
        "remove_activity_confirm": "Diese gesamte Aktivität samt aller Mini-Aktivitäten, Fotos, Notizen und Ausgaben wirklich löschen?",
        "back_activities": "← Zurück zu den Aktivitäten",
        "mini_activities_heading": "Mini-Aktivitäten",
        "no_minis": "Noch keine Mini-Aktivitäten erstellt. Füge unten eine hinzu!",
        "create_mini_drawer": "Mini-Aktivität erstellen",
        "mini_photo_label": "Foto *",
        "mini_name_label": "Name der Mini-Aktivität *",
        "mini_name_ph": "z. B. Weihnachtsplätzchen backen",
        "mini_max_label": "Max. Anzahl Teilnehmende *",
        "create_mini_btn": "Mini-Aktivität erstellen",
        "max_participants_badge": "Max. {max} Teilnehmende",
        "open_mini_btn": "Mini-Aktivität öffnen",
        "delete_mini_btn": "Mini-Aktivität löschen",
        "remove_mini_confirm": "Diese Mini-Aktivität samt aller Notizen und Kassen-Einträge wirklich löschen?",
        "act_gallery_heading": "Fotogalerie der Aktivität",
        "no_act_photos": "In dieser Galerie wurden noch keine Fotos hochgeladen.",
        "add_gallery_photo_drawer": "Foto zur Galerie hinzufügen",
        "gal_photo_label": "Foto auswählen *",
        "gal_caption_label": "Kurze Bildunterschrift (optional)",
        "gal_caption_ph": "z. B. Zimtsterne erste Runde",
        "upload_gal_btn": "Foto hochladen",
        "remove_photo_confirm": "Dieses Foto wirklich aus der Galerie löschen?",
        # Mini-Activity Workspace (Notes + Budget Tracker)
        "back_activity": "← Zurück zu {name}",
        "starter_note_title": "Teilnehmende / Participants",
        "starter_note_body": "Teilnehmende: ",
        "mini_notes_heading": "Gemeinsame Notizen & Planung",
        "no_mini_notes": "Noch keine Notizen vorhanden.",
        "add_note_drawer": "Neue Notiz schreiben",
        "note_title_label": "Thema / Überschrift (optional)",
        "note_title_ph": "z. B. Treffpunkt & Uhrzeit, Zutatenliste, Vereinbarter Beitrag",
        "note_body_label": "Notiz *",
        "note_body_ph": "Details, Uhrzeiten, Zutaten oder Namen der Teilnehmenden eintragen...",
        "note_pin_label": "Notiz oben anpinnen",
        "post_note_btn": "Notiz veröffentlichen",
        "edit_note_btn": "Bearbeiten",
        "upvote_title": "Für diese Notiz stimmen",
        "remove_note_confirm": "Diese Notiz wirklich löschen?",
        # Mini-Activity Budget Tracker
        "tracker_heading": "Gemeinsame Kasse & Ausgaben-Tracker",
        "tracker_balance_title": "Aktueller Kassenstand",
        "tracker_contrib_lbl": "Beiträge",
        "tracker_paid_count": "{paid}/{total} bezahlt (je {rate})",
        "tracker_net_spent_lbl": "Netto ausgegeben",
        "tracker_net_sub": "Entnommen − Zurückgelegt",
        "tracker_carry_lbl": "Übertrag",
        "tracker_carry_sub": "Startguthaben",
        "tracker_settings_drawer": "Vereinbarten Beitrag & Startguthaben festlegen",
        "tracker_rate_label": "Beitrag pro Person (€)",
        "tracker_start_label": "Übertragenes Startguthaben (€)",
        "tracker_save_settings": "Kassen-Einstellungen speichern",
        "tracker_members_heading": "Beiträge der Teilnehmenden",
        "tracker_members_hint": "Klicke auf einen Namen, um Bezahlt / Offen umzuschalten.",
        "tracker_paid_badge": "✓ Bezahlt",
        "tracker_unpaid_badge": "+ {rate}",
        "tracker_paid_on": "{rate} bezahlt ({date})",
        "tracker_click_pay": "Klicken, um {rate} als bezahlt zu markieren",
        "tracker_add_member_ph": "Name hinzufügen...",
        "tracker_add_member_btn": "+ Person hinzufügen",
        "tracker_edit_members_btn": "Namen bearbeiten / entfernen",
        "tracker_log_drawer": "Ausgabe & Kassenbon eintragen",
        "tracker_log_date": "Datum (TT/MM/JJJJ) *",
        "tracker_log_person": "Name der Person *",
        "tracker_log_person_ph": "z. B. Nesila",
        "tracker_took_out": "Entnommen (€)",
        "tracker_put_back": "Zurückgelegt / Wechselgeld (€)",
        "tracker_fund_change": "Kassenänderung",
        "tracker_items_total": "Summe Einzelposten",
        "tracker_items_heading": "Aufschlüsselung der Einkäufe (optional)",
        "tracker_add_item_btn": "+ Artikel hinzufügen",
        "tracker_item_ph": "Artikel (z. B. Mehl, Butter, Zucker)",
        "tracker_receipts_label": "Kassenbon-Foto(s) (bis zu 4)",
        "tracker_save_log_btn": "Eintrag speichern & Kasse aktualisieren",
        "tracker_logs_heading": "Ausgaben- & Beleg-Verlauf",
        "tracker_no_logs": "Noch keine Ausgaben eingetragen.",
        "tracker_log_summary": "hat {out} entnommen, {back} zurückgelegt",
        "tracker_no_items_listed": "Keine Einzelposten angegeben",
        "tracker_expenses_prefix": "Einkäufe",
        "tracker_view_receipt": "Ansehen",
        "tracker_delete_log_confirm": "Diesen Eintrag wirklich löschen? Der Kassenstand wird automatisch neu berechnet.",
        "close_modal": "Schließen ✕",
        # House Duties & Passwords
        "remove_entry": "Eintrag entfernen",
        "no_duties": "Derzeit sind keine Hausdienste eingetragen.",
        "add_duty": "Dienst eintragen",
        "duty_label": "Dienst",
        "contact_label": "Ansprechperson und Kontaktweg",
        "save": "Save",
        "to": "bis",
        "guests": "Gäste",
        "change_resident_pw": "Hauszugang ändern",
        "change_dienstleiter_pw": "Dienstleiter-Passwort ändern",
        "new_shared_pw": "Neues gemeinsames Passwort",
        "change_logout_residents": "Ändern und Bewohnerinnen abmelden",
        "change_logout_dienstleiter": "Ändern und Dienstleiter abmelden",
        "your_pw": "Eigenes Passwort",
        "current_pw": "Bisheriges Passwort",
        "new_pw": "Neues Passwort",
        "change_pw": "Passwort ändern",
        "create_admin": "Verwaltungskonto anlegen",
        "account_name": "Kontoname",
        "initial_pw": "Startpasswort",
        "create_account": "Konto anlegen",
        "revoke_access": "Zugriff entziehen",
        "page_unavailable": "Page not available.",
        "photo_too_large": "Foto zu groß.",
        "login_failed": "Anmeldung fehlgeschlagen.",
        "back_residents": "Zurück zum Hauszugang",
        "back_dienstleiter": "Zurück zu Dienstleiter",
        "go_admin": "Zur Verwaltung",
        "check_input": "Eingaben prüfen.",
        "check_input_body": "Bitte fülle alle Pflichtfelder aus, wähle freie Nächte und halte Freitexte innerhalb der Grenzen.",
        "back_overview": "Zur Übersicht",
    },
}
TEXT["de"]["save"] = "Speichern"
TEXT["de"]["page_unavailable"] = "Seite nicht verfügbar."

TEXT["en"].update({
    "skip_to_content": "Skip to content",
    "nav_main": "Main navigation",
    "tab_home": "Home",
    "tab_laundry": "Laundry",
    "tab_shelf": "Shelf",
    "tab_activities": "Activities",
    "tab_more": "More",
    "home_right_now": "Right now",
    "home_all_sections": "All sections",
    "home_laundry_free": "{free} of {total} free",
    "home_news_none": "No announcements yet.",
    "home_shelf_count": "{n} on the shelf",
    "home_shelf_empty": "The shelf is empty.",
    "home_guest_requests": "{n} booking request(s) waiting",
    "home_guest_none": "No open booking requests.",
    "home_guest_next": "Next booking: {arrival} – {departure}",
    "home_guest_rooms": "{n} guest room(s) from {price} / night",
    "home_guest_hint": "Open a room to see free nights and request dates.",
    "recent_taken_title": "Recently taken ({n})",
    "recent_taken_hint": "Taken items stay here for 7 days, so a mistaken click can be undone.",
    "taken_today": "Taken today",
    "taken_days": "Taken {days} day(s) ago",
    "put_back_btn": "Put back on shelf",
    "taken_notice": "This item was marked as taken. If that was a mistake, put it back.",
    "quick_start": "Start now for",
    "custom_start": "Other start time or length",
    "check_input_field": "Please check: {fields}.",
    "check_input_conflict": "Those nights are no longer free. Go back and choose other dates.",
    "back_to_form": "Back to the form",
    "too_many_logins": "Too many failed logins. Try again in {mins} minute(s).",
    "server_error": "Something went wrong.",
    "server_error_body": "The action could not be completed. Go back and try again; if it keeps happening, tell the Administration.",
})
TEXT["de"].update({
    "skip_to_content": "Zum Inhalt springen",
    "nav_main": "Hauptmenü",
    "tab_home": "Start",
    "tab_laundry": "Waschen",
    "tab_shelf": "Schrank",
    "tab_activities": "Aktivitäten",
    "tab_more": "Mehr",
    "home_right_now": "Gerade jetzt",
    "home_all_sections": "Alle Bereiche",
    "home_laundry_free": "{free} von {total} frei",
    "home_news_none": "Noch keine Mitteilungen.",
    "home_shelf_count": "{n} im Schrank",
    "home_shelf_empty": "Der Schrank ist leer.",
    "home_guest_requests": "{n} Buchungsanfrage(n) warten",
    "home_guest_none": "Keine offenen Buchungsanfragen.",
    "home_guest_next": "Nächste Buchung: {arrival} – {departure}",
    "home_guest_rooms": "{n} Gästezimmer ab {price} / Nacht",
    "home_guest_hint": "Zimmer öffnen, freie Nächte sehen und Termine anfragen.",
    "recent_taken_title": "Kürzlich mitgenommen ({n})",
    "recent_taken_hint": "Mitgenommene Gegenstände bleiben 7 Tage hier, damit ein versehentlicher Klick rückgängig gemacht werden kann.",
    "taken_today": "Heute mitgenommen",
    "taken_days": "Vor {days} Tag(en) mitgenommen",
    "put_back_btn": "Zurück in den Schrank",
    "taken_notice": "Dieser Gegenstand wurde als mitgenommen markiert. Falls das ein Versehen war, lege ihn zurück.",
    "quick_start": "Jetzt starten für",
    "custom_start": "Andere Startzeit oder Dauer",
    "check_input_field": "Bitte prüfen: {fields}.",
    "check_input_conflict": "Diese Nächte sind nicht mehr frei. Geh zurück und wähle andere Termine.",
    "back_to_form": "Zurück zum Formular",
    "too_many_logins": "Zu viele fehlgeschlagene Anmeldungen. Versuche es in {mins} Minute(n) erneut.",
    "server_error": "Etwas ist schiefgelaufen.",
    "server_error_body": "Die Aktion konnte nicht abgeschlossen werden. Geh zurück und versuche es erneut; wenn es weiter passiert, melde es der Verwaltung.",
})


def get_setting_pair(key):
    row = db.execute("SELECT en, de FROM settings WHERE key=?", (key,)).fetchone()
    if row:
        return row["en"], row["de"]
    return DEFAULT_SETTINGS.get(key, ("", ""))


def get_setting(key, lang):
    en_v, de_v = get_setting_pair(key)
    return de_v if lang == "de" else en_v


def get_shelf_max_days():
    en_v, _ = get_setting_pair("shelf_max_days")
    try:
        return max(1, int(en_v))
    except ValueError:
        return 60


def get_laundry_grace_mins():
    en_v, _ = get_setting_pair("laundry_grace_mins")
    try:
        return max(1, min(120, int(en_v)))
    except ValueError:
        return 10


def machine_title(m, lang):
    t = TEXT[lang]
    kind_lbl = t["machine_dryer"] if m["kind"] == "dryer" else t["machine_washer"]
    return f"{m['name']} ({kind_lbl})"


def machine_svg(kind):
    if kind == "dryer":
        return (
            '<svg class="machine-icon" viewBox="0 0 80 80" aria-hidden="true">'
            '<rect x="14" y="10" width="52" height="60" rx="8" fill="#ffffff" stroke="#000000" stroke-width="3.5"/>'
            '<line x1="14" y1="24" x2="66" y2="24" stroke="#000000" stroke-width="3"/>'
            '<circle cx="24" cy="17" r="2.5" fill="#000000"/><circle cx="32" cy="17" r="2.5" fill="#000000"/>'
            '<rect x="46" y="14.5" width="12" height="5" rx="2" fill="#ff4d8d"/>'
            '<circle cx="40" cy="47" r="16" fill="#ffe6f0" stroke="#000000" stroke-width="3.5"/>'
            '<path d="M33 43 Q37 39 40 43 T47 43 M33 51 Q37 47 40 51 T47 51" fill="none" stroke="#ff4d8d" stroke-width="2.5" stroke-linecap="round"/>'
            '</svg>'
        )
    return (
        '<svg class="machine-icon" viewBox="0 0 80 80" aria-hidden="true">'
        '<rect x="14" y="10" width="52" height="60" rx="8" fill="#ffffff" stroke="#000000" stroke-width="3.5"/>'
        '<line x1="14" y1="24" x2="66" y2="24" stroke="#000000" stroke-width="3"/>'
        '<circle cx="24" cy="17" r="2.5" fill="#000000"/><circle cx="32" cy="17" r="2.5" fill="#000000"/>'
        '<rect x="46" y="14.5" width="12" height="5" rx="2" fill="#ff4d8d"/>'
        '<circle cx="40" cy="47" r="16" fill="#e5faff" stroke="#000000" stroke-width="3.5"/>'
        '<path d="M26 48 Q33 43 40 48 T54 48 A14 14 0 0 1 26 48 Z" fill="#00d0ff" opacity="0.65"/>'
        '</svg>'
    )


def fmt_clock(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def sync_machines():
    now = int(time.time())
    grace_secs = get_laundry_grace_mins() * 60
    rows = list(db.execute("SELECT * FROM machines"))
    for m in rows:
        if m["status"] == "occupied" and now >= (m["end_ts"] or 0):
            if m["reserved"]:
                pend_end = (m["end_ts"] or now) + grace_secs
                if now >= pend_end:
                    db.execute("UPDATE machines SET status='free', start_ts=0, end_ts=0, reserved=0, pending_until=0 WHERE id=?", (m["id"],))
                else:
                    db.execute("UPDATE machines SET status='pending', start_ts=0, end_ts=0, reserved=0, pending_until=? WHERE id=?", (pend_end, m["id"]))
            else:
                db.execute("UPDATE machines SET status='free', start_ts=0, end_ts=0, reserved=0, pending_until=0 WHERE id=?", (m["id"],))
        elif m["status"] == "pending" and now >= (m["pending_until"] or 0):
            db.execute("UPDATE machines SET status='free', start_ts=0, end_ts=0, reserved=0, pending_until=0 WHERE id=?", (m["id"],))


def render_message_html(raw):
    lines = [ln.rstrip() for ln in (raw or "").splitlines()]
    out = []
    in_list = False
    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        if stripped.startswith(("- ", "* ", "• ")):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{esc(stripped[2:].strip())}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{esc(stripped)}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def message_block(key, lang, can_edit, next_path, wrapper_cls=""):
    t = TEXT[lang]
    en_v, de_v = get_setting_pair(key)
    active_txt = de_v if lang == "de" else en_v
    rendered = render_message_html(active_txt)
    if not rendered and not can_edit:
        return ""
    editor = ""
    if can_edit:
        editor = (
            f'<details class="inline-edit">'
            + icon_control("edit", t["edit_msg_btn"], tag="summary")
            + form(
                "save-setting",
                f'<input type="hidden" name="key" value="{esc(key)}">'
                f'<input type="hidden" name="next" value="{esc(next_path)}">'
                f'<label>{esc(t["msg_en_label"])}<textarea name="en" rows="3" maxlength="2000">{esc(en_v)}</textarea></label>'
                f'<label>{esc(t["msg_de_label"])}<textarea name="de" rows="3" maxlength="2000">{esc(de_v)}</textarea></label>'
                f'<small class="edit-hint">{esc(t["msg_bullet_hint"])}</small>'
                + button(t["save"]),
                extra='class="inline-edit-form"',
            )
            + "</details>"
        )
    if wrapper_cls:
        return f'<div class="{wrapper_cls}">{rendered}{editor}</div>'
    return f'<div class="editable-msg">{rendered}{editor}</div>'


def get_tags_map():
    rows = db.execute("SELECT key, en, de FROM tags ORDER BY rowid ASC").fetchall()
    return {r["key"]: (r["en"], r["de"]) for r in rows}


def tag_label(key, lang, tags_map):
    pair = tags_map.get(key)
    if not pair:
        return key
    return pair[1] if lang == "de" else pair[0]


def parse_tags(raw, tags_map):
    return [k for k in (raw or "").split(",") if k in tags_map]


def tag_pills_html(raw, lang, tags_map):
    keys = parse_tags(raw, tags_map)
    if not keys:
        return ""
    return '<div class="tag-list">' + "".join(
        f'<span class="tag-pill">{esc(tag_label(k, lang, tags_map))}</span>' for k in keys
    ) + "</div>"


def fmt_eur(amount):
    val = float(amount)
    return f"{int(val)} €" if val.is_integer() else f"{val:.2f} €"

def fmt_date(val):
    if not val:
        return ""
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val).strftime("%d.%m.%Y")
    s = str(val).strip()[:10]
    parts = s.split("-")
    if len(parts) == 3 and len(parts[0]) == 4:
        return f"{parts[2]}.{parts[1]}.{parts[0]}"
    return s


def parse_eur(raw):
    cleaned = str(raw or "").replace("€", "").strip().replace(",", ".")
    if not cleaned:
        return 0.0
    val = float(cleaned)
    if val < 0 or val > 100000:
        raise ValueError()
    return round(val, 2)


def fmt_money(amount, signed=False):
    val = round(float(amount or 0), 2)
    prefix = "+" if signed and val > 0 else ("-" if val < 0 else "")
    return f"{prefix}{abs(val):.2f} €".replace(".", ",")


def room_subtitle(room, lang):
    guests_word = "guests" if lang == "en" else "Gäste"
    night_word = "/night" if lang == "en" else "/Nacht"
    return f"Max. {room['max_guests']} {guests_word} · {fmt_eur(room['price'])}{night_word}"


def cleanup_expired_bookings():
    today_iso = date.today().isoformat()
    if db.in_transaction:
        db.execute("DELETE FROM bookings WHERE departure IS NOT NULL AND departure != '' AND departure < ?", (today_iso,))
    else:
        with db:
            db.execute("DELETE FROM bookings WHERE departure IS NOT NULL AND departure != '' AND departure < ?", (today_iso,))

def room_has_conflict(room_id, arrival, departure):
    cleanup_expired_bookings()
    rows = db.execute(
        "SELECT arrival, departure FROM bookings WHERE room_id=? AND status IN ('pending', 'approved')",
        (room_id,),
    ).fetchall()
    for r in rows:
        try:
            a = date.fromisoformat(r["arrival"])
            d = date.fromisoformat(r["departure"])
            if a < departure and d > arrival:
                return True
        except ValueError:
            continue
    return False


def delete_mini_cascade(mini_id):
    log_ids = [r[0] for r in db.execute("SELECT id FROM mini_logs WHERE mini_id=?", (mini_id,))]
    for lid in log_ids:
        db.execute("DELETE FROM mini_log_photos WHERE log_id=?", (lid,))
    db.execute("DELETE FROM mini_logs WHERE mini_id=?", (mini_id,))
    db.execute("DELETE FROM mini_members WHERE mini_id=?", (mini_id,))
    db.execute("DELETE FROM mini_notes WHERE mini_id=?", (mini_id,))
    db.execute("DELETE FROM mini_activities WHERE id=?", (mini_id,))


def delete_activity_cascade(act_id):
    mini_ids = [r[0] for r in db.execute("SELECT id FROM mini_activities WHERE activity_id=?", (act_id,))]
    for mid in mini_ids:
        delete_mini_cascade(mid)
    db.execute("DELETE FROM activity_photos WHERE activity_id=?", (act_id,))
    db.execute("DELETE FROM activities WHERE id=?", (act_id,))


def esc(s):
    return escape(str(s), quote=True)


def field(label, key, kind="text", extra=""):
    return f'<label>{label}<input type="{kind}" name="{key}" required {extra}></label>'


def button(label, cls=""):
    c = f' class="{cls}"' if cls else ""
    return f"<button{c}>{label}</button>"


def form(action, body, extra=""):
    return f'<form method="post" action="/{action}" {extra}>{body}</form>'


ICON_PATHS = {
    "edit": "M4 16 16 4l4 4L8 20l-5 1z M13 7l4 4",
    "trash": "M3 6h18 M9 6V3h6v3 M6 6l1 15h10l1-15 M10 10v7 M14 10v7",
    "pin": "M9 3h9l-2 6 3 4-6 1-6 7 3-9-4-3 4-1z",
    "vote": "M4 18 12 5l8 13z",
}


def icon_control(kind, label, *, tag="button", attrs="", count=None, pressed=None):
    label = esc(label)
    state = "" if pressed is None else f' aria-pressed="{str(bool(pressed)).lower()}"'
    number = "" if count is None else f'<span>{int(count)}</span>'
    return (f'<{tag} class="micro-action {kind}" aria-label="{label}" title="{label}" {attrs}{state}>'
            f'<svg aria-hidden="true" viewBox="0 0 24 24"><path d="{ICON_PATHS[kind]}"/></svg>'
            f'{number}</{tag}>')


def note_card(row, t, voted, *, mini_id=None, manage=True):
    mini = mini_id is not None
    prefix = "mini-note" if mini else "announcement"
    key = row["id"]
    fields = f'<input type="hidden" name="id" value="{key}">'
    if mini:
        fields += f'<input type="hidden" name="mini_id" value="{mini_id}">'
    actions = form(prefix + "-vote", fields + icon_control("vote", t["upvote_title"], count=row["votes"], pressed=voted))
    editor = ""
    pinned = bool(row["pinned"])
    if manage:
        editor_id = f"edit-{prefix}-{key}"
        actions += icon_control("edit", t["edit_note_btn"], attrs=f'type="button" popovertarget="{editor_id}"')
        required_title = "" if mini else "required"
        edit_fields = (
            f'<label>{esc(t["note_title_label"] if mini else t["ann_title_label"])}'
            f'<input name="title" maxlength="120" value="{esc(row["title"] or "")}" {required_title}></label>'
            f'<label>{esc(t["note_body_label"] if mini else t["ann_body_label"])}'
            f'<textarea name="body" maxlength="2000" required>{esc(row["body"])}</textarea></label>'
        )
        editor = (f'<div popover id="{editor_id}" class="note-editor">'
                  + form(prefix + "-edit", fields + edit_fields + button(t["save_changes"]))
                  + f'<button type="button" popovertarget="{editor_id}" popovertargetaction="hide">{esc(t["close_modal"])}</button></div>')
        actions += form("mini-note-pin" if mini else "toggle-pin-announcement",
                        fields + icon_control("pin", t["unpin_btn"] if pinned else t["pin_btn"], pressed=pinned))
        confirm = esc(json.dumps(t["remove_note_confirm" if mini else "remove_ann_confirm"]))
        actions += form("mini-note-delete" if mini else "remove-announcement", fields + icon_control("trash", t["remove"]),
                        extra=f'onsubmit="return confirm({confirm})"')
    stamp = datetime.fromtimestamp(row["created_at"] or time.time()).strftime("%d.%m.%Y")
    badge = f'<span class="ann-pin-badge">{esc(t["pinned_badge"])}</span>' if pinned else ""
    title = f'<h3>{esc(row["title"])}</h3>' if row["title"] else ""
    return (f'<article class="ann-card{" pinned" if pinned else ""}"><div class="ann-meta">{badge}<small>{stamp}</small></div>'
            f'{title}<p class="ann-body">{esc(row["body"])}</p><div class="note-tools">{actions}</div>{editor}</article>')


def panel(title, body, cls=""):
    c = f' class="{cls}"' if cls else ""
    return f"<section{c}><h2>{title}</h2>{body}</section>"


FIELD_LABELS = {
    "name": ("Name", "Name"),
    "arrival": ("Check-in date", "Anreisedatum"),
    "departure": ("Check-out date", "Abreisedatum"),
    "guests": ("Number of guests", "Anzahl Gäste"),
    "title": ("Title", "Titel"),
    "body": ("Text", "Text"),
    "password": ("Password", "Passwort"),
    "old": ("Current password", "Aktuelles Passwort"),
    "start_time": ("Start time", "Startzeit"),
    "hours": ("Duration", "Dauer"),
    "minutes": ("Duration", "Dauer"),
    "image": ("Photo", "Foto"),
    "price": ("Price", "Preis"),
    "max_guests": ("Maximum guests", "Maximale Gästezahl"),
    "floor": ("Room name", "Zimmername"),
    "duty": ("Duty", "Dienst"),
    "contact": ("Contact", "Kontakt"),
    "person": ("Person", "Person"),
    "log_date": ("Date", "Datum"),
    "max_participants": ("Maximum participants", "Maximale Teilnehmerzahl"),
    "en": ("English text", "Englischer Text"),
    "de": ("German text", "Deutscher Text"),
    "filename": ("File", "Datei"),
}

NAV_ITEMS = [
    ("home", "/", "overview"),
    ("rules", "/regeln", "rules_nav"),
    ("guest", "/gast", "guest_room"),
    ("duties", "/dienste", "house_duties"),
    ("activities", "/aktivitaeten", "activities_nav"),
    ("laundry", "/wasch", "laundry"),
    ("shelf", "/objekte", "shelf"),
]
SECTION_PREFIXES = (
    ("/regeln", "rules"), ("/gast", "guest"), ("/danke", "guest"), ("/dienste", "duties"),
    ("/aktivitaeten", "activities"), ("/mini", "activities"), ("/wasch", "laundry"),
    ("/objekte", "shelf"), ("/admin", "admin"),
)
TAB_ICONS = {
    "home": "M3 11.5 12 4l9 7.5 M5.5 10v10h13V10 M10 20v-5.5h4V20",
    "laundry": "M6 3h12a1.5 1.5 0 0 1 1.5 1.5v15A1.5 1.5 0 0 1 18 21H6a1.5 1.5 0 0 1-1.5-1.5v-15A1.5 1.5 0 0 1 6 3z M4.5 8h15 M12 18a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M8 5.5h.01 M11 5.5h.01",
    "shelf": "M4 4.5h16v15H4z M4 12h16 M9 8.2h6 M9 15.8h6",
    "activities": "M4.5 5.5h15v14h-15z M4.5 10h15 M8.5 3.5v4 M15.5 3.5v4 M9 14h.01 M12 14h.01 M15 14h.01",
    "more": "M5.5 12h.01 M12 12h.01 M18.5 12h.01",
}


def nav_section(path):
    if path == "/":
        return "home"
    for prefix, key in SECTION_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return key
    return ""


def _link(url, label, active, cls=""):
    cur = ' aria-current="page"' if active else ""
    klass = (cls + (" active" if active else "")).strip()
    attr = ' class="%s"' % klass if klass else ""
    return '<a href="%s"%s%s>%s</a>' % (url, attr, cur, esc(label))


def site_nav(t, section, show_admin):
    items = list(NAV_ITEMS) + ([("admin", "/admin", "admin_settings")] if show_admin else [])
    links = "".join(_link(url, t[label], key == section) for key, url, label in items)
    return '<nav class="site-nav" aria-label="%s">%s</nav>' % (esc(t["nav_main"]), links)


def tab_bar(t, section, show_admin):
    cells = ""
    for key, url, label in (("home", "/", "tab_home"), ("laundry", "/wasch", "tab_laundry"),
                            ("shelf", "/objekte", "tab_shelf"), ("activities", "/aktivitaeten", "tab_activities")):
        cur = ' aria-current="page"' if key == section else ""
        cells += ('<a class="tab tab-%s" href="%s"%s><svg aria-hidden="true" viewBox="0 0 24 24"><path d="%s"/></svg>'
                  '<span>%s</span></a>' % (key, url, cur, TAB_ICONS[key], esc(t[label])))
    more = [("rules", "/regeln", "rules_nav"), ("guest", "/gast", "guest_room"), ("duties", "/dienste", "house_duties")]
    if show_admin:
        more.append(("admin", "/admin", "admin_settings"))
    sheet = "".join(_link(url, t[label], key == section) for key, url, label in more)
    active = " active" if section in ("rules", "guest", "duties", "admin") else ""
    cells += ('<details class="more-menu%s"><summary class="tab tab-more"><svg aria-hidden="true" viewBox="0 0 24 24">'
              '<path d="%s"/></svg><span>%s</span></summary><div class="more-sheet">%s</div></details>'
              % (active, TAB_ICONS["more"], esc(t["tab_more"]), sheet))
    return '<div class="tabbar" role="navigation" aria-label="%s">%s</div>' % (esc(t["nav_main"]), cells)


def asset_url(name):
    try:
        version = "%x" % (STATIC / name).stat().st_mtime_ns
    except OSError:
        version = "0"
    return "/static/%s?v=%s" % (name, version)


def asset_script(name):
    return '<script src="%s"></script>' % asset_url(name)


def fmt_dur(minutes, t):
    h, m = divmod(int(minutes), 60)
    if h and m:
        return "%d %s %d" % (h, t["hours_short"], m)
    if h:
        return "%d %s" % (h, t["hours_short"])
    return "%d %s" % (m, t["mins_short"])


def purge_taken_items():
    """Items marked as taken stay recoverable for 7 days, then they are removed for good."""
    db.execute("DELETE FROM items WHERE taken_at > 0 AND taken_at < ?", (int(time.time()) - 7 * 86400,))


def taken_items_html(t):
    rows = list(db.execute("SELECT id, title, taken_at FROM items WHERE taken_at > 0 ORDER BY taken_at DESC"))
    if not rows:
        return ""
    now = int(time.time())
    out = ""
    for r in rows:
        days = max(0, (now - r["taken_at"]) // 86400)
        when = t["taken_today"] if days == 0 else t["taken_days"].format(days=days)
        out += (
            f'<li><img src="/foto/{r["id"]}?t=1" alt="" loading="lazy" decoding="async">'
            f'<span class="taken-title"><strong>{esc(r["title"])}</strong><span class="taken-when">{esc(when)}</span></span>'
            + form("restore-item", f'<input type="hidden" name="id" value="{r["id"]}">' + button(esc(t["put_back_btn"]), "btn-secondary btn-sm"))
            + "</li>"
        )
    return (
        f'<details class="drawer-box taken-box"><summary>{esc(t["recent_taken_title"].format(n=len(rows)))}</summary>'
        f'<div class="drawer-body"><p>{esc(t["recent_taken_hint"])}</p><ul class="taken-list">{out}</ul></div></details>'
    )


def home_dashboard(t, lang, is_admin):
    """The 'Right now' cards on the home page: laundry, news, shelf and guest room at a glance."""
    machines = list(db.execute("SELECT * FROM machines ORDER BY id ASC"))
    free = sum(1 for m in machines if m["status"] == "free")
    rows = ""
    for m in machines:
        st = m["status"]
        if st == "occupied":
            state = t["free_at_time"].format(time=fmt_clock(m["end_ts"]))
        elif st == "pending":
            state = t["pending_until_time"].format(time=fmt_clock(m["pending_until"]))
        else:
            state = t["status_free"]
        rows += (f'<li class="{st}"><span class="dot" aria-hidden="true"></span>'
                 f'<span class="m-name">{esc(machine_title(m, lang))}</span><span class="m-state">{esc(state)}</span></li>')
    if machines:
        laundry_body = (f'<p class="live-big">{esc(t["home_laundry_free"].format(free=free, total=len(machines)))}</p>'
                        f'<ul class="machine-dots">{rows}</ul>')
    else:
        laundry_body = f'<p>{esc(t["no_machines"])}</p>'
    laundry = f'<a class="live-card live-laundry" href="/wasch"><h3>{esc(t["laundry"])}</h3>{laundry_body}</a>'

    ann = db.execute("SELECT title, body, pinned FROM announcements ORDER BY pinned DESC, id DESC LIMIT 1").fetchone()
    if ann:
        excerpt = " ".join((ann["body"] or "").split())
        if len(excerpt) > 120:
            excerpt = excerpt[:117].rstrip() + "…"
        pin = f'<span class="live-pin">{esc(t["pinned_badge"])}</span>' if ann["pinned"] else ""
        news_body = f'<p class="live-big">{pin}{esc(ann["title"])}</p><p class="live-text">{esc(excerpt)}</p>'
    else:
        news_body = f'<p class="live-text">{esc(t["home_news_none"])}</p>'
    news = f'<a class="live-card live-news" href="/regeln"><h3>{esc(t["rules_nav"])}</h3>{news_body}</a>'

    count = db.execute("SELECT COUNT(*) FROM items WHERE taken_at = 0").fetchone()[0]
    recent = list(db.execute("SELECT id, title FROM items WHERE taken_at = 0 ORDER BY id DESC LIMIT 4"))
    if recent:
        thumbs = "".join(f'<img src="/foto/{r["id"]}?t=1" alt="{esc(r["title"])}" loading="lazy" decoding="async">' for r in recent)
        shelf_body = f'<p class="live-big">{esc(t["home_shelf_count"].format(n=count))}</p><div class="live-thumbs">{thumbs}</div>'
    else:
        shelf_body = f'<p class="live-text">{esc(t["home_shelf_empty"])}</p>'
    shelf = f'<a class="live-card live-shelf" href="/objekte"><h3>{esc(t["shelf"])}</h3>{shelf_body}</a>'

    cleanup_expired_bookings()
    extra = ""
    if is_admin:
        pending = db.execute("SELECT COUNT(*) FROM bookings WHERE status = 'pending'").fetchone()[0]
        nxt = db.execute("SELECT arrival, departure FROM bookings WHERE status = 'approved' AND departure >= ? ORDER BY arrival LIMIT 1",
                         (date.today().isoformat(),)).fetchone()
        big = t["home_guest_requests"].format(n=pending) if pending else t["home_guest_none"]
        small = t["home_guest_next"].format(arrival=fmt_date(nxt["arrival"]), departure=fmt_date(nxt["departure"])) if nxt else ""
        extra = " attention" if pending else ""
    else:
        prices = [r["price"] for r in db.execute("SELECT price FROM rooms")]
        big = t["home_guest_rooms"].format(n=len(prices), price=fmt_eur(min(prices))) if prices else ""
        small = t["home_guest_hint"]
    guest = (f'<a class="live-card live-guest{extra}" href="/gast"><h3>{esc(t["guest_room"])}</h3>'
             f'<p class="live-big">{esc(big)}</p><p class="live-text">{esc(small)}</p></a>')
    return f'<div class="live-grid">{laundry}{news}{shelf}{guest}</div>'


def page(title, body, nav="", lang="en", current_path="/", compact_title=False, level="", topnav="", tabbar=""):
    t = TEXT[lang]
    path_only = urlparse(current_path or "/").path
    next_q = quote(current_path or "/", safe="/?=&")
    en_cls = ' class="active" aria-current="true"' if lang == "en" else ""
    de_cls = ' class="active" aria-current="true"' if lang == "de" else ""
    switcher = (
        f'<span class="lang-switch" role="group" aria-label="Language">'
        f'<a href="/lang?set=de&next={next_q}" lang="de" hreflang="de"{de_cls}>DE</a>'
        f'<a href="/lang?set=en&next={next_q}" lang="en" hreflang="en"{en_cls}>EN</a>'
        f"</span>"
    )
    role_badge = ""
    if level == "dienstleiter":
        role_badge = f'<span class="role-badge dienstleiter">{esc(t["dienstleiter"])}</span>'
    elif level == "admin":
        role_badge = f'<span class="role-badge admin">{esc(t["admin"])}</span>'

    header_right = f'<div class="header-actions">{role_badge}{switcher}{nav}</div>'
    if path_only.startswith("/objekte"):
        theme_cls = "theme-1"
    elif path_only.startswith("/gast") or path_only.startswith("/danke"):
        theme_cls = "theme-2"
    elif path_only.startswith("/dienste"):
        theme_cls = "theme-3"
    elif path_only.startswith("/wasch"):
        theme_cls = "theme-5"
    elif path_only.startswith("/regeln"):
        theme_cls = "theme-6"
    elif path_only.startswith("/aktivitaeten") or path_only.startswith("/mini"):
        theme_cls = "theme-7"
    elif path_only.startswith("/dienstleiter"):
        theme_cls = "theme-dienstleiter"
    elif path_only.startswith("/admin"):
        theme_cls = "theme-4"
    else:
        theme_cls = ""
    classes = [theme_cls] if theme_cls else []
    if tabbar:
        classes.append("has-tabbar")
    body_attrs = []
    if classes:
        body_attrs.append(f'class="{" ".join(classes)}"')
    if level:
        body_attrs.append(f'data-role="{esc(level)}"')
    body_tag = f"<body {' '.join(body_attrs)}>" if body_attrs else "<body>"
    h1_cls = ' class="compact-title"' if compact_title else ""
    brand_icon = '<svg class="brand-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M3 11.5 12 4l9 7.5 M5.5 10v10h13V10 M10 20v-5.5h4V20"/></svg>'
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        f'<meta name="color-scheme" content="light dark"><meta name="theme-color" content="#e02a00">'
        f'<meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-capable" content="yes">'
        f'<meta name="apple-mobile-web-app-title" content="{esc(t["brand"])}">'
        f'<link rel="manifest" href="/manifest.webmanifest">'
        f'<link rel="icon" type="image/png" href="/static/icons/icon-192.png">'
        f'<link rel="apple-touch-icon" href="/apple-touch-icon.png">'
        f'<link rel="preload" href="/static/fonts/outfit-latin-wght-normal.woff2" as="font" type="font/woff2" crossorigin>'
        f'<link rel="stylesheet" href="{asset_url("style.css")}">'
        f"<title>{esc(title)} · {t['brand']}</title></head>"
        f"{body_tag}<a class=\"skip-link\" href=\"#main\">{esc(t['skip_to_content'])}</a>"
        f'<header class="site-header"><a class="brand" href="/">{brand_icon}<span>{t["brand"]}</span></a>{header_right}</header>'
        f"{topnav}"
        f'<main id="main"><h1{h1_cls}>{esc(title)}</h1>{body}</main>'
        f"<footer>{t['footer']}</footer>{tabbar}{asset_script('app.js')}</body></html>"
    )


class Portal(BaseHTTPRequestHandler):
    timeout = 30  # drop connections that stall, so one slow phone cannot block the others

    def log_message(self, *args):
        pass

    def reply(self, body, status=200, mime="text/html; charset=utf-8", cache="no-store", etag=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        if etag and status == 200 and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache)
            self.end_headers()
            return
        gz = False
        textual = mime.startswith("text/") or "javascript" in mime or "json" in mime or "svg" in mime
        if textual and len(data) > 800 and "gzip" in self.headers.get("Accept-Encoding", ""):
            data = gzip.compress(data, 6, mtime=0)
            gz = True
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        if etag:
            self.send_header("ETag", etag)
        if textual:
            self.send_header("Vary", "Accept-Encoding")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        if mime.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def image(self, data):
        """Send a stored photo. The browser keeps it and only re-checks (cheap 304) instead of re-downloading."""
        etag = '"%s"' % hashlib.blake2b(data, digest_size=10).hexdigest()
        return self.reply(data, mime="image/jpeg", cache="private, no-cache", etag=etag)

    def client_ip(self):
        if TRUST_PROXY:
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd:
                return fwd.split(",")[0].strip()
        return self.client_address[0]

    def serve_static(self, parsed):
        name = STATIC_ALIASES.get(parsed.path) or parsed.path[len("/static/"):]
        base = STATIC.resolve()
        try:
            file = (STATIC / name).resolve()
        except (OSError, ValueError):
            return self.reply("", 404)
        if base not in file.parents or not file.is_file() or file.suffix.lower() not in STATIC_TYPES:
            return self.reply("", 404)
        st = file.stat()
        etag = 'W/"%x-%x"' % (st.st_mtime_ns, st.st_size)
        versioned = "v=" in (parsed.query or "")
        cache = "public, max-age=31536000, immutable" if versioned else ("public, max-age=604800" if file.suffix == ".woff2" else "no-cache")
        return self.reply(file.read_bytes(), mime=STATIC_TYPES[file.suffix.lower()], cache=cache, etag=etag)

    def fail_500(self):
        try:
            t = TEXT[self.lang()]
            self.reply(page(t["server_error"], f'<p>{t["server_error_body"]}</p><p><a href="/">{t["back_overview"]}</a></p>',
                            lang=self.lang()), 500)
        except Exception:
            pass

    def do_GET(self):
        try:
            if not host_allowed(self.headers.get("Host", "")):
                return self.reply("", 403)
            parsed = urlparse(self.path)
            if parsed.path.startswith("/static/") or parsed.path in STATIC_ALIASES:
                return self.serve_static(parsed)
            with DB_LOCK:
                self._get()
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except Exception:
            traceback.print_exc()
            self.fail_500()

    def do_POST(self):
        try:
            host = self.headers.get("Host", "")
            if not host_allowed(host) or not origin_allowed(self.headers.get("Origin", ""), host):
                return self.reply("", 403)
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.reply("", 400)
            if size < 0 or size > 12000000:
                return self.reply(TEXT[self.lang()]["photo_too_large"], 413)
            self._raw_body = self.rfile.read(size)
            with DB_LOCK:
                self._post()
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except Exception:
            traceback.print_exc()
            self.fail_500()

    def _cookies(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            pass
        return jar

    def token(self):
        jar = self._cookies()
        return jar["session"].value if "session" in jar else ""

    def lang(self):
        jar = self._cookies()
        if "lang" in jar:
            return jar["lang"].value if jar["lang"].value in ("en", "de") else "en"
        for part in self.headers.get("Accept-Language", "").split(","):
            code = part.split(";")[0].strip().lower()[:2]
            if code in ("de", "en"):
                return code
        return "en"

    def role(self):
        name, until = sessions.get(self.token(), ("", 0))
        return name if until > time.time() else ""

    def access_level(self):
        r = self.role()
        if not r:
            return ""
        if r == "residents":
            return "residents"
        if r == "dienstleiter":
            return "dienstleiter"
        return "admin"

    def redirect(self, path="/", cookie=None, lang_cookie=None, max_age=None):
        self.send_response(303)
        secure = "; Secure" if SECURE_COOKIES else ""
        if cookie is not None:
            life = "; Max-Age=%d" % max_age if (cookie and max_age) else ("; Max-Age=0" if not cookie else "")
            self.send_header("Set-Cookie", "session=" + cookie + "; HttpOnly; SameSite=Strict; Path=/" + life + secure)
        if lang_cookie is not None:
            self.send_header("Set-Cookie", "lang=" + lang_cookie + "; SameSite=Strict; Path=/; Max-Age=31536000" + secure)
        self.send_header("Location", path)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _get(self):
        parsed = urlparse(self.path)
        path = parsed.path
        lang = self.lang()
        t = TEXT[lang]

        if path == "/lang":
            qs = parse_qs(parsed.query)
            choice = qs.get("set", ["en"])[0]
            dest = qs.get("next", ["/"])[0]
            if choice not in ("en", "de"):
                choice = "en"
            if not dest.startswith("/") or dest.startswith("//"):
                dest = "/"
            return self.redirect(dest, lang_cookie=choice)

        role = self.role()
        level = self.access_level()
        is_admin = level == "admin"
        can_manage_general = level in ("admin", "dienstleiter")
        can_manage_guestroom = is_admin
        can_manage_rules_news = is_admin
        can_manage_passwords = is_admin

        if not role:
            if path not in ("/", "/dienstleiter", "/admin"):
                return self.reply(t["please_login"], 403)
            if path == "/admin":
                login_fields = field(t["admin_account"], "account")
                panel_title = t["admin"]
                switch_links = (
                    f'<span class="role-switch-links">'
                    f'<a href="/">{t["residents"]}</a> · '
                    f'<a href="/dienstleiter">{t["dienstleiter"]}</a>'
                    f"</span>"
                )
            elif path == "/dienstleiter":
                login_fields = '<input type="hidden" name="account" value="dienstleiter">'
                panel_title = t["dienstleiter_access"]
                switch_links = (
                    f'<span class="role-switch-links">'
                    f'<a href="/">{t["residents"]}</a> · '
                    f'<a href="/admin">{t["admin"]}</a>'
                    f"</span>"
                )
            else:
                login_fields = '<input type="hidden" name="account" value="residents">'
                panel_title = t["resident_access"]
                switch_links = (
                    f'<span class="role-switch-links">'
                    f'<a href="/dienstleiter">{t["dienstleiter"]}</a> · '
                    f'<a href="/admin">{t["admin"]}</a>'
                    f"</span>"
                )
            body = panel(
                panel_title,
                form("login", login_fields + field(t["password"], "password", "password") + button(t["login"])),
            )
            return self.reply(page(t["welcome"], body, switch_links, lang=lang, current_path=path))

        if path.startswith("/foto/"):
            row = db.execute("SELECT image, thumb FROM items WHERE id=?", (path[6:],)).fetchone()
            if not row:
                return self.reply("", 404)
            small = "t=1" in (parsed.query or "").split("&") and row["thumb"]
            return self.image(row["thumb"] if small else row["image"])

        if path.startswith("/room-foto/"):
            row = db.execute("SELECT image FROM rooms WHERE id=?", (path[11:],)).fetchone()
            if not row:
                return self.reply("", 404)
            if row["image"]:
                return self.image(row["image"])
            return self.reply(PLACEHOLDER_ROOM_SVG, mime="image/svg+xml")

        if path.startswith("/act-foto/"):
            row = db.execute("SELECT image FROM activities WHERE id=?", (path[10:],)).fetchone()
            return self.image(row[0]) if row else self.reply("", 404)

        if path.startswith("/act-gallery-foto/"):
            row = db.execute("SELECT image FROM activity_photos WHERE id=?", (path[18:],)).fetchone()
            return self.image(row[0]) if row else self.reply("", 404)

        if path.startswith("/mini-foto/"):
            row = db.execute("SELECT image FROM mini_activities WHERE id=?", (path[11:],)).fetchone()
            return self.image(row[0]) if row else self.reply("", 404)

        if path.startswith("/receipt-foto/"):
            row = db.execute("SELECT image FROM mini_log_photos WHERE id=?", (path[14:],)).fetchone()
            return self.image(row[0]) if row else self.reply("", 404)

        if path == "/hausordnung.pdf":
            row = db.execute("SELECT filename, data FROM documents WHERE key='house_rules'").fetchone()
            if not row or not row["data"]:
                return self.reply(t["page_unavailable"], 404)
            fname = (row["filename"] or "Hausordnung.pdf").replace('"', "")
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", f'inline; filename="{fname}"')
            self.send_header("Content-Length", str(len(row["data"])))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(row["data"])
            return

        nav = form("logout", button(
            '<svg class="logout-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M9 4H5v16h4 M16 8l4 4-4 4 M20 12H9"/></svg>'
            f'<span class="logout-label">{t["logout"]}</span>'))
        section = nav_section(path)
        links = site_nav(t, section, can_manage_passwords)
        tabbar = tab_bar(t, section, can_manage_passwords)

        full_current_path = path + (f"?{parsed.query}" if parsed.query else "")

        if path == "/":
            cards = [
                (t["shelf"], t["card_shelf_desc"], "objekte"),
                (t["guest_room"], t["card_guest_desc"], "gast"),
                (t["house_duties"], t["card_duties_desc"], "dienste"),
                (t["laundry"], t["card_laundry_desc"], "wasch"),
                (t["rules_card_title"], t["card_rules_desc"], "regeln"),
                (t["activities_title"], t["card_activities_desc"], "aktivitaeten"),
            ]
            if can_manage_passwords:
                cards.append((t["admin_settings"], t["card_admin_desc"], "admin"))
            with db:
                sync_machines()
            body = (
                f'<h2 class="home-heading">{esc(t["home_right_now"])}</h2>'
                + home_dashboard(t, lang, can_manage_guestroom)
                + f'<h2 class="home-heading">{esc(t["home_all_sections"])}</h2>'
                + '<div class="grid">' + "".join(
                    f'<a class="card" href="/{url}"><h2>{title}</h2><p>{desc}</p></a>'
                    for title, desc, url in cards
                ) + "</div>"
            )
            title = t["our_house"]

        elif path == "/objekte":
            title = t["shelf"]
            tags_map = get_tags_map()
            max_days = get_shelf_max_days()
            qs = parse_qs(parsed.query)
            selected_tags = [k for k in qs.get("tag", []) if k in tags_map]
            now = int(time.time())

            with db:
                purge_taken_items()
            intro_html = message_block("shelf_intro", lang, can_manage_general, "/objekte")

            filter_checks = "".join(
                f'<label class="tag-check"><input type="checkbox" name="tag" value="{esc(k)}"'
                f'{" checked" if k in selected_tags else ""}><span>{esc(label_pair[1] if lang == "de" else label_pair[0])}</span></label>'
                for k, label_pair in tags_map.items()
            )
            filter_label = f"{t['filter_active']} ({len(selected_tags)})" if selected_tags else t["filter_btn"]
            filter_open = " open" if selected_tags else ""
            filter_ui = (
                f'<details class="filter-box"{filter_open}>'
                f"<summary>{esc(filter_label)}</summary>"
                f'<form method="get" action="/objekte" class="filter-form">'
                f'<div class="tag-grid">{filter_checks}</div>'
                f'<div class="filter-actions">'
                f'<button type="submit">{t["apply_filter"]}</button>'
                f'<a class="btn-secondary" href="/objekte">{t["show_all"]}</a>'
                f"</div></form></details>"
            )

            all_rows = list(db.execute("SELECT id, title, tags, created_at FROM items WHERE taken_at = 0 ORDER BY id DESC"))
            if selected_tags:
                rows = [r for r in all_rows if any(tg in selected_tags for tg in parse_tags(r["tags"], tags_map))]
            else:
                rows = all_rows

            cards_html = []
            for r in rows:
                age_days = max(0, (now - (r["created_at"] or now)) // 86400)
                is_overdue = can_manage_general and age_days >= max_days
                overdue_cls = " overdue" if is_overdue else ""
                cards_html.append(
                    f'<a class="item-card{overdue_cls}" href="/objekte/{r["id"]}">'
                    f'<img src="/foto/{r["id"]}?t=1" alt="{esc(r["title"])}" loading="lazy" decoding="async">'
                    f'<div class="item-card-body">'
                    f'<h3>{esc(r["title"])}</h3>'
                    f"</div></a>"
                )

            gallery_html = '<div class="gallery-grid">' + "".join(cards_html) + "</div>" if cards_html else ""
            if not rows:
                empty_msg = t["no_filtered_items"] if selected_tags and all_rows else t["no_items"]
                gallery_html = f"<p>{empty_msg}</p>"

            upload_checks = "".join(
                f'<label class="tag-check"><input type="checkbox" name="tags" value="{esc(k)}">'
                f"<span>{esc(label_pair[1] if lang == 'de' else label_pair[0])}</span></label>"
                for k, label_pair in tags_map.items()
            )
            upload_hint_html = message_block("post_item_hint", lang, can_manage_general, "/objekte")
            upload_drawer = (
                f'<details class="drawer-box">'
                f"<summary>{esc(t['post_item'])}</summary>"
                f'<div class="drawer-body">'
                f"{upload_hint_html}"
                + form(
                    "item",
                    f'<label>{t["photo_label"]}<input id="photo" type="file" accept="image/jpeg,image/png,image/webp" required></label>'
                    + field(t["item_title"], "title", extra='maxlength="120"')
                    + f'<label>{t["item_desc"]}<textarea name="description" rows="3" maxlength="900" placeholder="{esc(t["item_desc_ph"])}"></textarea></label>'
                    + f'<div class="tag-fieldset"><span class="field-title">{t["select_tags"]}</span>'
                    + f'<details class="tag-menu" id="tag-menu"><summary id="tag-menu-label" data-placeholder="{esc(t["choose_tags_menu"])}">{esc(t["choose_tags_menu"])}</summary>'
                    + f'<div class="tag-menu-panel"><div class="tag-grid">{upload_checks}</div></div></details></div>'
                    + '<input type="hidden" name="image" id="image"><input type="hidden" name="thumb" id="thumb">'
                    + f'<button type="submit" id="post-submit" disabled>{t["publish"]}</button>',
                    extra='id="shelf-upload-form"',
                )
                + "</div></details>"
            )

            admin_shelf_drawer = ""
            if can_manage_general:
                confirm_rm_tag = esc(t["remove_tag_confirm"])
                tag_rows_html = "".join(
                    f'<div class="tag-admin-row">'
                    f"<span><strong>{esc(en_v)}</strong> / {esc(de_v)}</span>"
                    + form(
                        "remove-tag",
                        f'<input type="hidden" name="key" value="{esc(k)}">' + button(t["remove"], "btn-reject btn-sm"),
                        extra=f'onsubmit="return confirm(\'{confirm_rm_tag}\')"',
                    )
                    + "</div>"
                    for k, (en_v, de_v) in tags_map.items()
                )
                admin_shelf_drawer = (
                    f'<details class="drawer-box">'
                    f"<summary>{esc(t['manage_shelf_admin'])}</summary>"
                    f'<div class="drawer-body">'
                    + form(
                        "save-setting",
                        f'<input type="hidden" name="key" value="shelf_max_days">'
                        f'<input type="hidden" name="next" value="/objekte">'
                        f'<label>{esc(t["shelf_days_label"])}<input type="number" name="en" min="1" max="3650" value="{max_days}" required></label>'
                        + button(t["save"]),
                    )
                    + f'<hr class="drawer-sep"><h3>{esc(t["add_tag_title"])}</h3>'
                    + form(
                        "add-tag",
                        field(t["tag_en_label"], "en", extra='maxlength="50"')
                        + field(t["tag_de_label"], "de", extra='maxlength="50"')
                        + button(t["add_tag_btn"]),
                    )
                    + f'<hr class="drawer-sep"><h3>{esc(t["existing_tags_title"])}</h3>'
                    + f'<div class="tag-admin-list">{tag_rows_html}</div>'
                    + "</div></details>"
                )

            shelf_js = asset_script("photo.js")
            body = f'{intro_html}{filter_ui}{gallery_html}{upload_drawer}{taken_items_html(t)}{admin_shelf_drawer}{shelf_js}'

        elif path.startswith("/objekte/") and path[9:].isdigit():
            item_id = int(path[9:])
            row = db.execute("SELECT id, title, description, tags, created_at, taken_at FROM items WHERE id=?", (item_id,)).fetchone()
            if not row:
                return self.reply(t["page_unavailable"], 404)
            tags_map = get_tags_map()
            max_days = get_shelf_max_days()
            title = row["title"]
            now = int(time.time())
            age_days = max(0, (now - (row["created_at"] or now)) // 86400)
            age_text = t["posted_today"] if age_days == 0 else t["posted_days"].format(days=age_days)
            is_overdue = age_days >= max_days
            overdue_cls = " overdue" if (can_manage_general and is_overdue) else ""
            badge_lbl = t["overdue_badge"].format(days=max_days)
            overdue_tag = f'<span class="overdue-badge">{esc(badge_lbl)}</span>' if (can_manage_general and is_overdue) else ""
            confirm_msg = esc(t["taken_confirm"])
            if row["taken_at"]:
                item_action = (
                    f'<p class="msg-banner">{esc(t["taken_notice"])}</p>'
                    + form("restore-item", f'<input type="hidden" name="id" value="{row["id"]}">' + button(esc(t["put_back_btn"])))
                )
            else:
                item_action = form(
                    "take-item",
                    f'<input type="hidden" name="id" value="{row["id"]}">' + button(t["taken_btn"], "btn-taken"),
                    extra=f'onsubmit="return confirm(\'{confirm_msg}\')"',
                )

            body = (
                f'<p><a href="/objekte">{t["back_shelf"]}</a></p>'
                f'<section class="detail-card{overdue_cls}">'
                f'<img class="detail-photo" src="/foto/{row["id"]}" alt="{esc(row["title"])}">'
                f"{overdue_tag}"
                f'<p class="item-meta">{esc(age_text)}</p>'
                f'{tag_pills_html(row["tags"], lang, tags_map)}'
                + (f'<p class="item-desc">{esc(row["description"])}</p>' if (row["description"] or "").strip() else "")
                + item_action
                + "</section>"
            )

        elif path == "/gast":
            cleanup_expired_bookings()
            title = t["guest_room"]
            rules_content = message_block("guest_rules", lang, can_manage_guestroom, "/gast")
            rules_box = (
                f'<div class="rules-banner">'
                f"<strong>{esc(t['guest_rules_title'])}</strong>"
                f"{rules_content}"
                f"</div>"
            )
            rooms = list(db.execute("SELECT id, floor, max_guests, price FROM rooms ORDER BY id"))
            confirm_rm_room = esc(t["remove_room_confirm"])
            if rooms:
                cards = []
                for r in rooms:
                    action_label = t["book_room_admin_btn"] if can_manage_guestroom else t["send_booking_btn"]
                    admin_card_tools = ""
                    if can_manage_guestroom:
                        admin_card_tools = (
                            f'<details class="card-edit-drawer room-edit">'
                            + icon_control("edit", t["edit_room_btn"], tag="summary")
                            + form(
                                "edit-room",
                                f'<input type="hidden" name="id" value="{r["id"]}">'
                                f'<label>{esc(t["room_floor_label"])}<input type="text" name="floor" maxlength="100" value="{esc(r["floor"])}" required></label>'
                                f'<label>{esc(t["room_max_label"])}<input type="number" name="max_guests" min="1" max="20" value="{r["max_guests"]}" required></label>'
                                f'<label>{esc(t["room_price_label"])}<input type="number" name="price" min="0" step="0.5" value="{r["price"]}" required></label>'
                                f'<label>{esc(t["replace_photo_label"])}<input type="file" class="edit-room-photo" accept="image/jpeg,image/png,image/webp"></label>'
                                '<input type="hidden" name="image" class="edit-room-image" value="">'
                                + button(t["save_changes"], "btn-sm"),
                                extra='class="edit-room-form"',
                            )
                            + "</details>"
                            + form(
                                "remove-room",
                                f'<input type="hidden" name="id" value="{r["id"]}">' + icon_control("trash", t["remove"]),
                                extra=f'onsubmit="return confirm(\'{confirm_rm_room}\')"',
                            )
                        )
                    cards.append(
                        f'<article class="room-card">'
                        f'<img src="/room-foto/{r["id"]}" alt="{esc(r["floor"])}">'
                        f'<div class="room-card-body">'
                        f'<h3>{esc(r["floor"])}</h3><p class="room-subtitle">{esc(room_subtitle(r, lang))}</p>'
                        f'<a class="btn-book-room" href="/gast/{r["id"]}">{esc(action_label)}</a>'
                        f'<div class="room-tools">{admin_card_tools}</div>'
                        f"</div></article>"
                    )
                rooms_html = f'<div class="gallery-grid">{"".join(cards)}</div>'
            else:
                rooms_html = f"<p>{t['no_rooms']}</p>"

            admin_guest_drawers = ""
            if can_manage_guestroom:
                pending_rows = list(
                    db.execute(
                        """SELECT b.*, r.floor FROM bookings b
                           LEFT JOIN rooms r ON b.room_id = r.id
                           WHERE b.status = 'pending'
                           ORDER BY b.arrival ASC"""
                    )
                )
                qs_gast = parse_qs(parsed.query)
                sort_by = qs_gast.get("sort", ["soonest"])[0]
                order_sql = "b.approved_at DESC, b.id DESC" if sort_by == "latest" else "b.arrival ASC, b.departure ASC"
                approved_rows = list(
                    db.execute(
                        f"""SELECT b.*, r.floor FROM bookings b
                            LEFT JOIN rooms r ON b.room_id = r.id
                            WHERE b.status = 'approved'
                            ORDER BY {order_sql}"""
                    )
                )

                pending_cards = []
                for b in pending_rows:
                    rm_label = b["floor"] if b["floor"] else f"Room #{b['room_id']}"
                    bed_txt = t["bedding_yes"] if b["bedding"] else t["bedding_no"]
                    note_html = f'<p class="booking-note">{esc(b["notes"])}</p>' if (b["notes"] or "").strip() else ""
                    pending_cards.append(
                        f'<article class="booking-admin-card pending">'
                        f'<div class="booking-admin-top"><b>{esc(b["name"])} · {esc(rm_label)}</b><span class="status-badge pending">{esc(t["status_pending"])}</span></div>'
                        f'<p>{esc(fmt_date(b["arrival"]))} {t["to"]} {esc(fmt_date(b["departure"]))} · {t["guests"]}: {b["guests"]} · {esc(bed_txt)} · <strong>{fmt_eur(b["total_price"])}</strong></p>'
                        f"{note_html}"
                        f'<div class="admin-btn-row">'
                        + form("approve-booking", f'<input type="hidden" name="id" value="{b["id"]}">' + button(t["approve_btn"], "btn-approve btn-sm"))
                        + form("reject-booking", f'<input type="hidden" name="id" value="{b["id"]}">' + button(t["reject_btn"], "btn-reject btn-sm"))
                        + "</div></article>"
                    )

                confirm_cancel_bk = esc(t["cancel_booking_confirm"])
                approved_cards = []
                for b in approved_rows:
                    rm_label = b["floor"] if b["floor"] else f"Room #{b['room_id']}"
                    bed_txt = t["bedding_yes"] if b["bedding"] else t["bedding_no"]
                    note_html = f'<p class="booking-note">{esc(b["notes"])}</p>' if (b["notes"] or "").strip() else ""
                    paid = bool(b["paid"])
                    payment_label = t["tracker_paid_badge"] if paid else t["booking_unpaid"]
                    payment_form = form(
                        "booking-payment",
                        f'<input type="hidden" name="id" value="{b["id"]}">'
                        f'<input type="hidden" name="paid" value="{int(not paid)}">'
                        f'<input type="hidden" name="sort" value="{esc(sort_by)}">'
                        f'<button class="btn-sm booking-payment" aria-pressed="{str(paid).lower()}">'
                        f'{esc(payment_label)}</button>',
                    )
                    approved_cards.append(
                        f'<article id="booking-{b["id"]}" class="booking-admin-card approved">'
                        f'<div class="booking-admin-top"><b>{esc(b["name"])} · {esc(rm_label)}</b><span class="status-badge approved">{esc(t["status_approved"])}</span></div>'
                        f'<p>{esc(fmt_date(b["arrival"]))} {t["to"]} {esc(fmt_date(b["departure"]))} · {t["guests"]}: {b["guests"]} · {esc(bed_txt)} · <strong>{fmt_eur(b["total_price"])}</strong></p>'
                        f"{note_html}"
                        f'<div class="admin-btn-row">'
                        + payment_form
                        + form(
                            "reject-booking",
                            f'<input type="hidden" name="id" value="{b["id"]}"><input type="hidden" name="sort" value="{esc(sort_by)}">'
                            + button(t["free_dates_btn"], "btn-reject btn-sm"),
                            extra=f'onsubmit="return confirm(\'{confirm_cancel_bk}\')"',
                        )
                        + "</div></article>"
                    )

                requests_drawer = (
                    f'<details class="drawer-box">'
                    f"<summary>{esc(t['admin_requests_drawer'])} ({len(pending_rows)})</summary>"
                    f'<div class="drawer-body">{"".join(pending_cards) or "<p>" + t["no_requests"] + "</p>"}</div>'
                    f"</details>"
                )

                soon_cls = "sort-pill active" if sort_by != "latest" else "sort-pill"
                late_cls = "sort-pill active" if sort_by == "latest" else "sort-pill"
                sort_bar = (
                    f'<div class="booking-sort-bar">'
                    f'<span>{esc(t["sort_label"])}</span>'
                    f'<a class="{soon_cls}" href="/gast?sort=soonest">{esc(t["sort_soonest"])}</a>'
                    f'<a class="{late_cls}" href="/gast?sort=latest">{esc(t["sort_latest_approved"])}</a>'
                    f"</div>"
                )
                approved_open = " open" if "sort" in qs_gast else ""
                approved_drawer = (
                    f'<details class="drawer-box"{approved_open}>'
                    f"<summary>{esc(t['admin_approved_drawer'])} ({len(approved_rows)})</summary>"
                    f'<div class="drawer-body">{sort_bar}{"".join(approved_cards) or "<p>" + t["no_approved"] + "</p>"}</div>'
                    f"</details>"
                )

                add_room_drawer = (
                    f'<details class="drawer-box">'
                    f"<summary>{esc(t['admin_add_room_drawer'])}</summary>"
                    f'<div class="drawer-body">'
                    f"<p>{esc(t['admin_rooms_hint'])}</p>"
                    + form(
                        "add-room",
                        f'<label>{t["room_photo_label"]}<input id="room-photo" type="file" accept="image/jpeg,image/png,image/webp" required></label>'
                        + f'<label>{t["room_floor_label"]}<input type="text" name="floor" id="room-floor" maxlength="100" placeholder="{esc(t["room_floor_ph"])}" required></label>'
                        + f'<label>{t["room_max_label"]}<input type="number" name="max_guests" id="room-max" min="1" max="20" required></label>'
                        + f'<label>{t["room_price_label"]}<input type="number" name="price" id="room-price" min="0" step="0.5" required></label>'
                        + f'<p class="room-format-preview">{esc(t["room_preview_label"])}: <strong id="room-preview-text">—</strong><span id="room-preview-sub" class="room-subtitle"></span></p>'
                        + '<input type="hidden" name="image" id="room-image">'
                        + f'<button type="submit" id="room-submit" disabled>{t["add_room_btn"]}</button>',
                        extra='id="add-room-form"',
                    )
                    + "</div></details>"
                )

                thanks_drawer = (
                    f'<details class="drawer-box">'
                    f"<summary>{esc(t['admin_thanks_msg_drawer'])}</summary>"
                    f'<div class="drawer-body">{message_block("booking_thanks", lang, can_manage_guestroom, "/gast")}</div>'
                    f"</details>"
                )

                admin_guest_drawers = f"{requests_drawer}{approved_drawer}{add_room_drawer}{thanks_drawer}"

            body = f'{rules_box}{rooms_html}{admin_guest_drawers}{asset_script("photo.js")}'

        elif path.startswith("/gast/") and path[6:].isdigit():
            cleanup_expired_bookings()
            room_id = int(path[6:])
            room = db.execute("SELECT id, floor, max_guests, price FROM rooms WHERE id=?", (room_id,)).fetchone()
            if not room:
                return self.reply(t["page_unavailable"], 404)
            title = room["floor"]

            day_status = {}
            for b in db.execute(
                "SELECT arrival, departure, status FROM bookings WHERE room_id=? AND status IN ('pending', 'approved')",
                (room_id,),
            ):
                try:
                    cur = date.fromisoformat(b["arrival"])
                    end = date.fromisoformat(b["departure"])
                    st = b["status"]
                    while cur < end:
                        iso = cur.isoformat()
                        if day_status.get(iso) != "approved":
                            day_status[iso] = st
                        cur += timedelta(days=1)
                except ValueError:
                    continue

            today_iso = date.today().isoformat()
            cal_json = esc(json.dumps(day_status))
            admin_note_html = (
                f'<p class="admin-mode-badge">{esc(t["admin_auto_approve_note"])}</p>' if can_manage_guestroom else ""
            )
            name_ph = t["admin_name_ph"] if can_manage_guestroom else t["resident_name_ph"]
            submit_lbl = t["book_directly_btn"] if can_manage_guestroom else t["send_request_submit"]

            body = (
                f'<p><a href="/gast">{t["back_guest_rooms"]}</a></p>'
                f'<section class="booking-panel">'
                f'<div class="booking-room-header">'
                f'<img src="/room-foto/{room["id"]}" alt="{esc(title)}">'
                f'<div><p class="room-subtitle">{esc(room_subtitle(room, lang))}</p>'
                f"{admin_note_html}{message_block('calendar_hint', lang, is_admin, path)}</div>"
                f"</div>"
                + form(
                    "book",
                    f'<input type="hidden" name="room_id" value="{room["id"]}">'
                    f'<div class="calendar-wrapper" id="room-calendar" '
                    f'data-status="{cal_json}" data-today="{today_iso}" data-lang="{lang}" '
                    f'data-price="{float(room["price"])}" data-bedding-fee="{BEDDING_FEE}">'
                    f'<div class="cal-header">'
                    f'<button type="button" class="cal-nav" id="cal-prev" aria-label="Previous month">‹</button>'
                    f'<strong id="cal-month-label"></strong>'
                    f'<button type="button" class="cal-nav" id="cal-next" aria-label="Next month">›</button>'
                    f"</div>"
                    f'<div class="cal-weekdays" id="cal-weekdays"></div>'
                    f'<div class="cal-days" id="cal-days"></div>'
                    f'<div class="cal-legend">'
                    f'<span><i class="dot free"></i>{esc(t["legend_free"])}</span>'
                    f'<span><i class="dot pending"></i>{esc(t["legend_pending"])}</span>'
                    f'<span><i class="dot occupied"></i>{esc(t["legend_occupied"])}</span>'
                    f"</div>"
                    f"</div>"
                    f'<div class="date-pair">'
                    f'<label>{esc(t["checkin_label"])}<input type="date" name="arrival" id="book-arrival" min="{today_iso}" required></label>'
                    f'<label>{esc(t["checkout_label"])}<input type="date" name="departure" id="book-departure" min="{today_iso}" required></label>'
                    f"</div>"
                    f'<p class="stay-summary" id="stay-summary" data-night="{esc(t["night_singular"])}" data-nights="{esc(t["night_plural"])}" data-prefix="{esc(t["nights_selected"])}"></p>'
                    f'<label>{esc(t["guests_count"])}<input type="number" name="guests" id="book-guests" min="1" max="{room["max_guests"]}" value="1" required></label>'
                    f'<label class="tag-check bedding-box"><input type="checkbox" name="bedding" value="1" id="book-bedding"><span>{esc(t["bedding_option"])}</span></label>'
                    f'<div class="price-box"><span>{esc(t["sum_to_pay"])}:</span> <strong id="total-price-display">0 €</strong></div>'
                    f'<label>{esc(t["resident_name_room"])}<input type="text" name="name" id="book-name" maxlength="80" placeholder="{esc(name_ph)}" required></label>'
                    f'<label>{esc(t["special_requests"])}<textarea name="notes" id="book-notes" rows="3" maxlength="900" placeholder="{esc(t["special_requests_ph"])}"></textarea></label>'
                    f'<button type="submit" id="booking-submit" disabled>{esc(submit_lbl)}</button>',
                    extra='id="guest-booking-form"',
                )
                + '</section>' + asset_script("photo.js")
            )

        elif path == "/danke":
            title = t["request_recorded"]
            thanks_msg = message_block("booking_thanks", lang, can_manage_guestroom, "/danke")
            body = (
                f"<section>{thanks_msg}"
                f'<p><a href="/gast">{t["back_guest_rooms"]}</a></p></section>'
            )

        elif path == "/wasch":
            title = t["laundry"]
            with db:
                sync_machines()
            grace_mins = get_laundry_grace_mins()
            rules_content = message_block("laundry_rules", lang, can_manage_general, "/wasch")
            rules_box = (
                f'<div class="rules-banner laundry-rules">'
                f"<strong>{esc(t['laundry_rules_title'])}</strong>"
                f"{rules_content}"
                f"</div>"
            )
            machines = list(db.execute("SELECT * FROM machines ORDER BY id ASC"))
            confirm_rm_m = esc(t["remove_machine_confirm"])
            m_cards = []
            for m in machines:
                st = m["status"]
                m_title = machine_title(m, lang)
                if st == "occupied":
                    status_badge = f'<span class="m-badge occupied">{esc(t["status_machine_occ"])}</span>'
                    time_info = (
                        f'<p class="m-time-main">{esc(t["free_at_time"].format(time=fmt_clock(m["end_ts"])))}</p>'
                        f'<p class="m-countdown" data-until="{m["end_ts"]}" data-prefix="{esc(t["time_left"])}"></p>'
                    )
                    if m["reserved"]:
                        res_html = (
                            f'<div class="m-reserved-row">'
                            f'<span class="m-reserved-tag">{esc(t["reserved_badge"])}</span>'
                            + form("machine-unreserve", f'<input type="hidden" name="id" value="{m["id"]}">' + button(t["cancel_reserve_btn"], "btn-secondary btn-sm"))
                            + "</div>"
                        )
                    else:
                        res_html = form("machine-reserve", f'<input type="hidden" name="id" value="{m["id"]}">' + button(t["reserve_next_btn"], "btn-secondary"))
                    action_html = (
                        f'<div class="m-actions">'
                        + form("machine-end", f'<input type="hidden" name="id" value="{m["id"]}">' + button(t["ended_early_btn"], "btn-approve"))
                        + res_html
                        + "</div>"
                    )
                else:
                    if st == "pending":
                        status_badge = f'<span class="m-badge pending">{esc(t["status_machine_pend"])}</span>'
                        time_info = (
                            f'<p class="m-time-main">{esc(t["pending_until_time"].format(time=fmt_clock(m["pending_until"])))}</p>'
                            f'<p class="m-countdown" data-until="{m["pending_until"]}" data-prefix="{esc(t["time_left"])}"></p>'
                            f'<p class="m-pending-note">{esc(t["pending_hint"])}</p>'
                        )
                    else:
                        status_badge = f'<span class="m-badge free">{esc(t["status_free"])}</span>'
                        time_info = ""
                    quick_mins = (30, 60, 90, 120) if m["kind"] == "dryer" else (60, 90, 120, 150)
                    chips = "".join(
                        f'<button type="submit" name="dur" value="{q}" class="chip" formnovalidate>{esc(fmt_dur(q, t))}</button>'
                        for q in quick_mins
                    )
                    action_html = form(
                        "machine-start",
                        f'<input type="hidden" name="id" value="{m["id"]}">'
                        f'<p class="m-quick-label">{esc(t["quick_start"])}</p>'
                        f'<div class="m-quick">{chips}</div>'
                        f'<details class="m-custom"><summary>{esc(t["custom_start"])}</summary>'
                        f'<div class="m-form-row">'
                        f'<label>{esc(t["start_time_label"])}<input type="time" name="start_time" class="m-start-input" required></label>'
                        f'<div class="m-dur-group"><span class="field-title">{esc(t["duration_label"])}</span>'
                        f'<div class="m-dur-inputs">'
                        f'<input type="number" name="hours" class="m-hours-input" min="0" max="12" value="1" aria-label="Hours" required><span>{esc(t["hours_short"])}</span>'
                        f'<input type="number" name="minutes" class="m-mins-input" min="0" max="59" value="30" aria-label="Minutes" required><span>{esc(t["mins_short"])}</span>'
                        f"</div></div></div>"
                        f'<p class="m-end-preview" data-prefix="{esc(t["ends_at_preview"])}"></p>'
                        + button(t["start_machine_btn"], "m-start-submit")
                        + "</details>",
                        extra='class="machine-start-form"',
                    )

                admin_m_tools = ""
                if can_manage_general:
                    sel_w = " selected" if m["kind"] == "washer" else ""
                    sel_d = " selected" if m["kind"] == "dryer" else ""
                    admin_m_tools = (
                        f'<details class="card-edit-drawer laundry-edit">'
                        f"<summary>{esc(t['edit_machine_btn'])}</summary>"
                        + form(
                            "edit-machine",
                            f'<input type="hidden" name="id" value="{m["id"]}">'
                            f'<label>{esc(t["machine_name_label"])}<input type="text" name="name" maxlength="60" value="{esc(m["name"])}" required></label>'
                            f'<label>{esc(t["machine_kind_label"])}<select name="kind">'
                            f'<option value="washer"{sel_w}>{esc(t["machine_washer"])}</option>'
                            f'<option value="dryer"{sel_d}>{esc(t["machine_dryer"])}</option>'
                            f"</select></label>"
                            + button(t["save_changes"], "btn-sm"),
                            extra='class="edit-room-form"',
                        )
                        + "</details>"
                        + form(
                            "remove-machine",
                            f'<input type="hidden" name="id" value="{m["id"]}">' + button(t["remove"], "btn-reject btn-sm"),
                            extra=f'onsubmit="return confirm(\'{confirm_rm_m}\')"',
                        )
                    )

                m_cards.append(
                    f'<article class="machine-card {st}">'
                    f'<div class="machine-card-top">{machine_svg(m["kind"])}'
                    f'<div><h3>{esc(m_title)}</h3>{status_badge}</div></div>'
                    f'<div class="machine-card-body">{time_info}{action_html}{admin_m_tools}</div>'
                    f"</article>"
                )

            machines_html = f'<div class="gallery-grid">{"".join(m_cards)}</div>' if m_cards else f"<p>{t['no_machines']}</p>"
            admin_laundry_drawer = ""
            if can_manage_general:
                admin_laundry_drawer = (
                    f'<details class="drawer-box">'
                    f"<summary>{esc(t['manage_laundry_admin'])}</summary>"
                    f'<div class="drawer-body">'
                    + form(
                        "save-setting",
                        f'<input type="hidden" name="key" value="laundry_grace_mins">'
                        f'<input type="hidden" name="next" value="/wasch">'
                        f'<label>{esc(t["grace_mins_label"])}<input type="number" name="en" min="1" max="120" value="{grace_mins}" required></label>'
                        + button(t["save"]),
                    )
                    + f'<hr class="drawer-sep"><h3>{esc(t["add_machine_title"])}</h3>'
                    + form(
                        "add-machine",
                        f'<label>{esc(t["machine_name_label"])}<input type="text" name="name" maxlength="60" placeholder="{esc(t["machine_name_ph"])}" required></label>'
                        f'<label>{esc(t["machine_kind_label"])}<select name="kind">'
                        f'<option value="washer">{esc(t["machine_washer"])}</option>'
                        f'<option value="dryer">{esc(t["machine_dryer"])}</option>'
                        f"</select></label>"
                        + button(t["add_machine_btn"]),
                    )
                    + "</div></details>"
                )
            laundry_js = asset_script("photo.js")
            body = f"{rules_box}{machines_html}{admin_laundry_drawer}{laundry_js}"

        elif path == "/regeln":
            title = t["rules_card_title"].replace("\u00ad", "")
            ann_rows = list(db.execute("SELECT * FROM announcements ORDER BY pinned DESC, id DESC"))
            votes = session_votes.get(self.token(), set())
            ann_cards = [note_card(a, t, ("announcement", a["id"]) in votes, manage=can_manage_rules_news)
                         for a in ann_rows]

            ann_list_html = (
                f'<div class="ann-list">{"".join(ann_cards)}</div>'
                if ann_cards
                else f"<p>{t['no_announcements']}</p>"
            )

            add_ann_drawer = ""
            if can_manage_rules_news:
                add_ann_drawer = (
                    f'<details class="drawer-box">'
                    f"<summary>{esc(t['add_announcement_title'])}</summary>"
                    f'<div class="drawer-body">'
                    + form(
                        "add-announcement",
                        f'<label>{esc(t["ann_title_label"])}<input type="text" name="title" maxlength="120" required></label>'
                        f'<label>{esc(t["ann_body_label"])}<textarea name="body" rows="4" maxlength="2000" required></textarea></label>'
                        f'<label class="tag-check"><input type="checkbox" name="pinned" value="1"><span>{esc(t["ann_pin_label"])}</span></label>'
                        + button(t["publish_ann_btn"]),
                    )
                    + "</div></details>"
                )

            rules_text_html = message_block("house_rules_text", lang, can_manage_rules_news, "/regeln")
            pdf_row = db.execute("SELECT filename FROM documents WHERE key='house_rules'").fetchone()
            pdf_btn_html = (
                f'<p><a class="btn-pdf" href="/hausordnung.pdf" target="_blank" rel="noopener">{esc(t["open_rules_pdf"])}</a></p>'
                if pdf_row
                else ""
            )

            admin_pdf_html = ""
            if can_manage_rules_news:
                current_pdf_note = (
                    f'<p><small>{esc(t["pdf_current"])}: <strong>{esc(pdf_row["filename"])}</strong></small></p>'
                    if pdf_row
                    else ""
                )
                remove_pdf_form = (
                    form("remove-rules-pdf", button(t["remove_pdf_btn"], "btn-reject btn-sm"))
                    if pdf_row
                    else ""
                )
                admin_pdf_html = (
                    f'<hr class="drawer-sep">'
                    f'<h3>{esc(t["manage_rules_pdf"])}</h3>'
                    f"{current_pdf_note}"
                    + form(
                        "upload-rules-pdf",
                        f'<label>{esc(t["pdf_upload_label"])}<input type="file" id="rules-pdf-input" accept="application/pdf" required></label>'
                        f'<input type="hidden" name="filename" id="rules-pdf-filename">'
                        f'<input type="hidden" name="pdf_b64" id="rules-pdf-b64">'
                        + f'<button type="submit" id="rules-pdf-submit" class="btn-sm" disabled>{esc(t["upload_pdf_btn"])}</button>',
                        extra='id="rules-pdf-form"',
                    )
                    + remove_pdf_form
                )

            rules_drawer = (
                f'<details class="drawer-box rules-drawer">'
                f"<summary>{esc(t['house_rules_drawer'])}</summary>"
                f'<div class="drawer-body">{rules_text_html}{pdf_btn_html}{admin_pdf_html}</div>'
                f"</details>"
            )

            rules_js = asset_script("photo.js") if can_manage_rules_news else ""
            script_tag = f"{rules_js}" if rules_js else ""

            body = (
                f'<section><h2>{esc(t["announcements_heading"])}</h2>{ann_list_html}</section>'
                f"{add_ann_drawer}{rules_drawer}{script_tag}"
            )

        elif path == "/aktivitaeten":
            title = t["activities_title"]
            intro_html = message_block("activities_intro", lang, can_manage_general, "/aktivitaeten")
            act_rows = list(db.execute("SELECT id, title FROM activities ORDER BY id DESC"))
            confirm_rm_act = esc(t["remove_activity_confirm"])
            if act_rows:
                act_cards = []
                for a in act_rows:
                    mini_cnt = db.execute("SELECT COUNT(*) FROM mini_activities WHERE activity_id=?", (a["id"],)).fetchone()[0]
                    act_cards.append(
                        f'<article class="room-card act-card">'
                        f'<img src="/act-foto/{a["id"]}" alt="{esc(a["title"])}">'
                        f'<div class="room-card-body">'
                        f'<h3><a class="card-link" href="/aktivitaeten/{a["id"]}">{esc(a["title"])}</a></h3>'
                        f'<p class="item-meta activity-count">{esc(t["mini_activities_heading"])}: {mini_cnt}</p>'
                        f'<div class="act-card-actions">'
                        + form(
                            "remove-activity",
                            f'<input type="hidden" name="id" value="{a["id"]}">' + icon_control("trash", t["remove"]),
                            extra=f'onsubmit="return confirm(\'{confirm_rm_act}\')"',
                        )
                        + "</div></div></article>"
                    )
                acts_html = f'<div class="gallery-grid">{"".join(act_cards)}</div>'
            else:
                acts_html = f"<p>{esc(t['no_activities'])}</p>"

            add_act_drawer = (
                f'<details class="drawer-box">'
                f"<summary>{esc(t['add_activity_drawer'])}</summary>"
                f'<div class="drawer-body">'
                + form(
                    "add-activity",
                    f'<label>{esc(t["act_photo_label"])}<input id="act-photo" type="file" accept="image/jpeg,image/png,image/webp" required></label>'
                    + f'<label>{esc(t["act_name_label"])}<input type="text" name="title" id="act-title" maxlength="100" placeholder="{esc(t["act_name_ph"])}" required></label>'
                    + '<input type="hidden" name="image" id="act-image">'
                    + f'<button type="submit" id="act-submit" disabled>{esc(t["add_activity_btn"])}</button>',
                    extra='id="add-activity-form"',
                )
                + "</div></details>"
            )

            act_js = asset_script("photo.js")
            body = f"{intro_html}{acts_html}{add_act_drawer}{act_js}"

        elif path.startswith("/aktivitaeten/") and path[14:].isdigit():
            act_id = int(path[14:])
            act = db.execute("SELECT id, title FROM activities WHERE id=?", (act_id,)).fetchone()
            if not act:
                return self.reply(t["page_unavailable"], 404)
            title = act["title"]

            confirm_rm_act = esc(t["remove_activity_confirm"])
            top_bar = (
                f'<div class="act-top-bar">'
                f'<a href="/aktivitaeten">{esc(t["back_activities"])}</a>'
                + form(
                    "remove-activity",
                    f'<input type="hidden" name="id" value="{act_id}">' + icon_control("trash", t["delete_activity_btn"]),
                    extra=f'onsubmit="return confirm(\'{confirm_rm_act}\')"',
                )
                + "</div>"
            )

            minis = list(
                db.execute(
                    "SELECT id, title, max_participants FROM mini_activities WHERE activity_id=? ORDER BY id DESC",
                    (act_id,),
                )
            )
            confirm_rm_mini = esc(t["remove_mini_confirm"])
            if minis:
                mini_cards = []
                for m in minis:
                    mini_cards.append(
                        f'<article class="room-card act-card">'
                        f'<img src="/mini-foto/{m["id"]}" alt="{esc(m["title"])}">'
                        f'<div class="room-card-body">'
                        f'<h3><a class="card-link" href="/mini/{m["id"]}">{esc(m["title"])}</a></h3>'
                        f'<div class="act-card-actions">'
                        + form(
                            "remove-mini-activity",
                            f'<input type="hidden" name="id" value="{m["id"]}">'
                            f'<input type="hidden" name="activity_id" value="{act_id}">'
                            + icon_control("trash", t["remove"]),
                            extra=f'onsubmit="return confirm(\'{confirm_rm_mini}\')"',
                        )
                        + "</div></div></article>"
                    )
                minis_html = f'<div class="gallery-grid">{"".join(mini_cards)}</div>'
            else:
                minis_html = f"<p>{esc(t['no_minis'])}</p>"

            create_mini_drawer = (
                f'<details class="drawer-box">'
                f"<summary>{esc(t['create_mini_drawer'])}</summary>"
                f'<div class="drawer-body">'
                + form(
                    "add-mini-activity",
                    f'<input type="hidden" name="activity_id" value="{act_id}">'
                    + f'<label>{esc(t["mini_photo_label"])}<input id="mini-photo" type="file" accept="image/jpeg,image/png,image/webp" required></label>'
                    + f'<label>{esc(t["mini_name_label"])}<input type="text" name="title" id="mini-title" maxlength="100" placeholder="{esc(t["mini_name_ph"])}" required></label>'
                    + f'<label>{esc(t["mini_max_label"])}<input type="number" name="max_participants" id="mini-max" min="1" max="200" value="10" required></label>'
                    + '<input type="hidden" name="image" id="mini-image">'
                    + f'<button type="submit" id="mini-submit" disabled>{esc(t["create_mini_btn"])}</button>',
                    extra='id="add-mini-form"',
                )
                + "</div></details>"
            )

            # Big-Activity Photo Gallery
            gal_hint_html = message_block("activity_gallery_hint", lang, can_manage_general, f"/aktivitaeten/{act_id}")
            gal_rows = list(
                db.execute(
                    "SELECT id, caption FROM activity_photos WHERE activity_id=? ORDER BY id DESC",
                    (act_id,),
                )
            )
            if gal_rows:
                gal_cards = []
                for gp in gal_rows:
                    cap_txt = (gp["caption"] or "").strip()
                    cap_html = f'<p class="gal-caption">{esc(cap_txt)}</p>' if cap_txt else ""
                    gal_cards.append(
                        f'<article class="gal-photo-card">'
                        f'<button type="button" class="gal-photo-btn js-lightbox-trigger" '
                        f'data-photo-id="{gp["id"]}" data-src="/act-gallery-foto/{gp["id"]}" data-caption="{esc(cap_txt or act["title"])}">'
                        f'<img src="/act-gallery-foto/{gp["id"]}" alt="{esc(cap_txt or act["title"])}">'
                        f"</button>"
                        f'{cap_html}</article>'
                    )
                gal_grid_html = f'<div class="gallery-grid">{"".join(gal_cards)}</div>'
            else:
                gal_grid_html = f"<p>{esc(t['no_act_photos'])}</p>"

            gal_upload_drawer = (
                f'<details class="drawer-box">'
                f"<summary>{esc(t['add_gallery_photo_drawer'])}</summary>"
                f'<div class="drawer-body">'
                + form(
                    "add-activity-photo",
                    f'<input type="hidden" name="activity_id" value="{act_id}">'
                    + f'<label>{esc(t["gal_photo_label"])}<input id="gal-photo" type="file" accept="image/jpeg,image/png,image/webp" required></label>'
                    + f'<label>{esc(t["gal_caption_label"])}<input type="text" name="caption" maxlength="140" placeholder="{esc(t["gal_caption_ph"])}"></label>'
                    + '<input type="hidden" name="image" id="gal-image">'
                    + f'<button type="submit" id="gal-submit" disabled>{esc(t["upload_gal_btn"])}</button>',
                    extra='id="act-gallery-form"',
                )
                + "</div></details>"
            )

            photo_delete = form(
                "remove-activity-photo",
                '<input type="hidden" name="id" id="gallery-delete-id">'
                + f'<input type="hidden" name="activity_id" value="{act_id}">'
                + icon_control("trash", t["remove"]),
                extra=f'onsubmit="return confirm({esc(json.dumps(t["remove_photo_confirm"]))})"',
            ) if can_manage_general else ""
            lightbox_modal = (
                f'<div id="receipt-modal" class="lightbox-backdrop hidden">'
                f'<div class="lightbox-box">'
                f'<div class="lightbox-top">'
                f'<span id="receipt-modal-caption"></span>'
                f'<button type="button" id="receipt-modal-close" class="btn-secondary btn-sm">{esc(t["close_modal"])}</button>'
                f"{photo_delete}</div>"
                f'<img id="receipt-modal-img" src="" alt="Preview">'
                f"</div></div>"
            )

            act_js = asset_script("photo.js")
            body = (
                f"{top_bar}"
                f"<h2>{esc(t['mini_activities_heading'])}</h2>"
                f"{minis_html}"
                f"{create_mini_drawer}"
                f'<hr class="drawer-sep">'
                f"<h2>{esc(t['act_gallery_heading'])}</h2>"
                f"{gal_hint_html}"
                f"{gal_grid_html}"
                f"{gal_upload_drawer}"
                f"{lightbox_modal}"
                f"{act_js}"
            )

        elif path.startswith("/mini/") and path[6:].isdigit():
            mini_id = int(path[6:])
            mini = db.execute(
                """SELECT m.*, a.title AS activity_title
                   FROM mini_activities m
                   JOIN activities a ON m.activity_id = a.id
                   WHERE m.id = ?""",
                (mini_id,),
            ).fetchone()
            if not mini:
                return self.reply(t["page_unavailable"], 404)
            title = mini["title"]
            act_id = mini["activity_id"]
            back_lbl = t["back_activity"].format(name=mini["activity_title"])
            cap_lbl = t["max_participants_badge"].format(max=mini["max_participants"])
            confirm_rm_mini = esc(t["remove_mini_confirm"])

            top_bar = (
                f'<div class="act-top-bar">'
                f'<a href="/aktivitaeten/{act_id}">{esc(back_lbl)}</a>'
                + form(
                    "remove-mini-activity",
                    f'<input type="hidden" name="id" value="{mini_id}">'
                    f'<input type="hidden" name="activity_id" value="{act_id}">'
                    + icon_control("trash", t["delete_mini_btn"]),
                    extra=f'onsubmit="return confirm(\'{confirm_rm_mini}\')"',
                )
                + "</div>"
            )

            mini_header = (
                f'<div class="booking-room-header mini-hero">'
                f'<img src="/mini-foto/{mini_id}" alt="{esc(title)}">'
                f'<div><span class="mini-cap-badge">{esc(cap_lbl)}</span>'
                f"{message_block('mini_notes_hint', lang, is_admin, path)}</div>"
                f"</div>"
            )

            # 1. Shared Notes Board
            notes_rows = list(
                db.execute(
                    "SELECT * FROM mini_notes WHERE mini_id=? ORDER BY pinned DESC, id DESC",
                    (mini_id,),
                )
            )
            voted_set = session_votes.get(self.token(), set())
            note_cards = [note_card(n, t, n["id"] in voted_set, mini_id=mini_id) for n in notes_rows]

            notes_list_html = (
                f'<div class="ann-list">{"".join(note_cards)}</div>'
                if note_cards
                else f"<p>{esc(t['no_mini_notes'])}</p>"
            )

            add_note_drawer = (
                f'<details class="drawer-box">'
                f"<summary>{esc(t['add_note_drawer'])}</summary>"
                f'<div class="drawer-body">'
                + form(
                    "mini-note-add",
                    f'<input type="hidden" name="mini_id" value="{mini_id}">'
                    f'<label>{esc(t["note_title_label"])}<input type="text" name="title" maxlength="120" placeholder="{esc(t["note_title_ph"])}"></label>'
                    f'<label>{esc(t["note_body_label"])}<textarea name="body" rows="3" maxlength="2000" placeholder="{esc(t["note_body_ph"])}" required></textarea></label>'
                    f'<label class="tag-check"><input type="checkbox" name="pinned" value="1"><span>{esc(t["note_pin_label"])}</span></label>'
                    + button(t["post_note_btn"]),
                )
                + "</div></details>"
            )

            notes_section = (
                f'<section class="mini-notes-panel">'
                f"<h2>{esc(t['mini_notes_heading'])}</h2>"
                f"{notes_list_html}"
                f"</section>"
                f"{add_note_drawer}"
            )

            # 2. Integrated Budget & Expense Tracker
            rate = float(mini["contribution"] or 0)
            carry_over = float(mini["starting_balance"] or 0)
            members = list(db.execute("SELECT * FROM mini_members WHERE mini_id=? ORDER BY id ASC", (mini_id,)))
            logs = list(db.execute("SELECT * FROM mini_logs WHERE mini_id=? ORDER BY id DESC", (mini_id,)))

            paid_members = [m for m in members if m["paid"]]
            contributions_total = round(len(paid_members) * rate, 2)
            total_took_out = round(sum(float(l["took_out"] or 0) for l in logs), 2)
            total_put_back = round(sum(float(l["put_back"] or 0) for l in logs), 2)
            net_spent = round(total_took_out - total_put_back, 2)
            current_balance = round(carry_over + contributions_total - net_spent, 2)

            rate_str = fmt_money(rate)
            paid_sub = t["tracker_paid_count"].format(paid=len(paid_members), total=len(members), rate=rate_str)

            balance_card = (
                f'<div class="tracker-balance-card">'
                f'<div class="tracker-bal-hero">'
                f'<div class="tracker-bal-kicker">{esc(t["tracker_balance_title"])}</div>'
                f'<div class="tracker-bal-amount">{esc(fmt_money(current_balance))}</div>'
                f'</div>'
                f'<div class="tracker-bal-grid">'
                f'<div><div class="tracker-stat-lbl">{esc(t["tracker_contrib_lbl"])}</div>'
                f'<div class="tracker-stat-val">{esc(fmt_money(contributions_total))}</div>'
                f'<div class="tracker-stat-sub">{esc(paid_sub)}</div></div>'
                f'<div><div class="tracker-stat-lbl">{esc(t["tracker_net_spent_lbl"])}</div>'
                f'<div class="tracker-stat-val">{esc(fmt_money(net_spent))}</div>'
                f'<div class="tracker-stat-sub">{esc(t["tracker_net_sub"])}</div></div>'
                f'<div><div class="tracker-stat-lbl">{esc(t["tracker_carry_lbl"])}</div>'
                f'<div class="tracker-stat-val">{esc(fmt_money(carry_over))}</div>'
                f'<div class="tracker-stat-sub">{esc(t["tracker_carry_sub"])}</div></div>'
                f"</div></div>"
            )

            rate_input_val = f"{rate:.2f}".replace(".", ",")
            carry_input_val = f"{carry_over:.2f}".replace(".", ",")
            settings_drawer = (
                f'<details class="drawer-box">'
                f"<summary>{esc(t['tracker_settings_drawer'])}</summary>"
                f'<div class="drawer-body">'
                + form(
                    "mini-budget-settings",
                    f'<input type="hidden" name="mini_id" value="{mini_id}">'
                    f'<div class="date-pair">'
                    f'<label>{esc(t["tracker_rate_label"])}<input type="text" inputmode="decimal" name="contribution" value="{esc(rate_input_val)}" required></label>'
                    f'<label>{esc(t["tracker_start_label"])}<input type="text" inputmode="decimal" name="starting_balance" value="{esc(carry_input_val)}" required></label>'
                    f"</div>"
                    + button(t["tracker_save_settings"], "btn-sm"),
                )
                + "</div></details>"
            )

            member_cards = []
            for mem in members:
                is_paid = bool(mem["paid"])
                m_cls = "tracker-member-btn paid" if is_paid else "tracker-member-btn"
                date_txt = (
                    f"✓ {fmt_date(mem['paid_date'])}" if (is_paid and mem["paid_date"])
                    else (t["tracker_paid_badge"] if is_paid else t["tracker_unpaid_badge"].format(rate=rate_str))
                )
                member_cards.append(
                    f'<div class="tracker-member-item">'
                    + form(
                        "mini-member-toggle",
                        f'<input type="hidden" name="id" value="{mem["id"]}">'
                        f'<input type="hidden" name="mini_id" value="{mini_id}">'
                        f'<button type="submit" class="{m_cls}">'
                        f'<div class="tracker-mem-name">{esc(mem["name"])}</div>'
                        f'<div class="tracker-mem-date">{esc(date_txt)}</div>'
                        f"</button>",
                        extra='class="tracker-toggle-form"',
                    )
                    + form(
                        "mini-member-remove",
                        f'<input type="hidden" name="id" value="{mem["id"]}">'
                        f'<input type="hidden" name="mini_id" value="{mini_id}">'
                        f'<button type="submit" class="tracker-mem-del" title="{esc(t["remove"])}">✕</button>',
                        extra='class="tracker-del-form"',
                    )
                    + "</div>"
                )

            members_grid_html = (
                f'<div class="tracker-members-grid">{"".join(member_cards)}</div>'
                if member_cards
                else ""
            )

            add_member_form = form(
                "mini-member-add",
                f'<input type="hidden" name="mini_id" value="{mini_id}">'
                f'<div class="tracker-add-row">'
                f'<input type="text" name="name" maxlength="60" placeholder="{esc(t["tracker_add_member_ph"])}" required>'
                f'<button type="submit">{esc(t["tracker_add_member_btn"])}</button>'
                f"</div>",
            )

            today_ddmmyyyy = date.today().strftime("%d/%m/%Y")
            datalist_opts = "".join(f'<option value="{esc(mem["name"])}"></option>' for mem in members)

            log_expense_drawer = (
                f'<details class="drawer-box">'
                f"<summary>{esc(t['tracker_log_drawer'])}</summary>"
                f'<div class="drawer-body">'
                + form(
                    "mini-log-add",
                    f'<input type="hidden" name="mini_id" value="{mini_id}">'
                    f'<input type="hidden" name="items_json" id="tracker-items-json" value="[]">'
                    f'<input type="hidden" name="receipts_json" id="tracker-receipts-json" value="[]">'
                    f'<div class="date-pair">'
                    f'<label>{esc(t["tracker_log_date"])}<input type="text" name="log_date" value="{today_ddmmyyyy}" maxlength="20" required></label>'
                    f'<label>{esc(t["tracker_log_person"])}<input type="text" name="person" list="mini-member-list" maxlength="80" placeholder="{esc(t["tracker_log_person_ph"])}" required></label>'
                    f"</div>"
                    f'<datalist id="mini-member-list">{datalist_opts}</datalist>'
                    f'<div class="date-pair">'
                    f'<label>{esc(t["tracker_took_out"])}<input type="text" inputmode="decimal" name="took_out" id="tracker-took-out" placeholder="10,00"></label>'
                    f'<label>{esc(t["tracker_put_back"])}<input type="text" inputmode="decimal" name="put_back" id="tracker-put-back" placeholder="2,50"></label>'
                    f"</div>"
                    f'<div id="tracker-formula-preview" class="tracker-formula-bar" '
                    f'data-change-lbl="{esc(t["tracker_fund_change"])}" data-items-lbl="{esc(t["tracker_items_total"])}">'
                    f'<span>{esc(t["tracker_fund_change"])}: <strong>0,00 €</strong></span>'
                    f'<span> | </span>'
                    f'<span>{esc(t["tracker_items_total"])}: <strong>0,00 €</strong></span>'
                    f"</div>"
                    f'<div class="tracker-items-block">'
                    f'<div class="tracker-items-head">'
                    f'<span class="field-title">{esc(t["tracker_items_heading"])}</span>'
                    f'<button type="button" id="tracker-add-item-btn" class="btn-secondary btn-sm">{esc(t["tracker_add_item_btn"])}</button>'
                    f"</div>"
                    f'<div id="tracker-item-rows" data-placeholder="{esc(t["tracker_item_ph"])}"></div>'
                    f"</div>"
                    f'<label>{esc(t["tracker_receipts_label"])}'
                    f'<input type="file" id="tracker-receipt-input" accept="image/jpeg,image/png,image/webp" multiple></label>'
                    f'<div id="tracker-receipt-previews" class="tracker-thumb-row"></div>'
                    + f'<button type="submit" id="tracker-log-submit">{esc(t["tracker_save_log_btn"])}</button>',
                    extra='id="tracker-log-form"',
                )
                + "</div></details>"
            )

            log_cards = []
            for lg in logs:
                net_val = round(float(lg["put_back"] or 0) - float(lg["took_out"] or 0), 2)
                net_cls = "tracker-net-badge neg" if net_val < 0 else "tracker-net-badge pos"
                try:
                    parsed_items = json.loads(lg["items_json"] or "[]")
                except ValueError:
                    parsed_items = []
                if parsed_items:
                    items_txt = ", ".join(
                        f"{esc(it.get('name') or 'Item')} ({esc(fmt_money(it.get('amount', 0)))})"
                        for it in parsed_items
                    )
                else:
                    items_txt = esc(t["tracker_no_items_listed"])

                photos = list(db.execute("SELECT id FROM mini_log_photos WHERE log_id=? ORDER BY id ASC", (lg["id"],)))
                thumbs_html = ""
                if photos:
                    cap_prefix = f"{lg['log_date']} · {lg['person']}"
                    thumbs_html = '<div class="tracker-thumb-row">' + "".join(
                        f'<button type="button" class="tracker-thumb-btn js-lightbox-trigger" '
                        f'data-src="/receipt-foto/{p["id"]}" data-caption="{esc(cap_prefix)}">'
                        f'<img src="/receipt-foto/{p["id"]}" alt="Receipt">'
                        f'<span>{esc(t["tracker_view_receipt"])}</span></button>'
                        for p in photos
                    ) + "</div>"

                summary_line = t["tracker_log_summary"].format(
                    out=fmt_money(lg["took_out"]),
                    back=fmt_money(lg["put_back"]),
                )
                log_cards.append(
                    f'<article class="tracker-log-card">'
                    f'<div class="tracker-log-top">'
                    f'<div><div class="tracker-log-date">{esc(fmt_date(lg["log_date"]))}</div>'
                    f'<div class="tracker-log-person"><strong>{esc(lg["person"])}</strong> — {esc(summary_line)}</div></div>'
                    f'<div class="tracker-log-right">'
                    f'<span class="{net_cls}">{esc(fmt_money(net_val, signed=True))}</span>'
                    f"</div></div>"
                    f'<div class="tracker-log-items"><strong>{esc(t["tracker_expenses_prefix"])}:</strong> {items_txt}</div>'
                    f"{thumbs_html}"
                    f"</article>"
                )

            logs_feed_html = (
                f'<div class="ann-list">{"".join(log_cards)}</div>'
                if log_cards
                else f"<p>{esc(t['tracker_no_logs'])}</p>"
            )

            tracker_section = (
                f'<details class="drawer-box tracker-collapse">'
                f"<summary>{esc(t['tracker_heading'])}</summary>"
                f'<div class="tracker-panel">'
                f"{balance_card}"
                f"{settings_drawer}"
                f'<div class="tracker-box">'
                f"<h3>{esc(t['tracker_members_heading'])}</h3>"
                f"<p>{esc(t['tracker_members_hint'])}</p>"
                f"{members_grid_html}"
                f"{add_member_form}"
                f"</div>"
                f"{log_expense_drawer}"
                f'<div class="tracker-box">'
                f"<h3>{esc(t['tracker_logs_heading'])}</h3>"
                f"{logs_feed_html}"
                f"</div>"
                f"</div>"
                f"</details>"
            )

            lightbox_modal = (
                f'<div id="receipt-modal" class="lightbox-backdrop hidden">'
                f'<div class="lightbox-box">'
                f'<div class="lightbox-top">'
                f'<span id="receipt-modal-caption"></span>'
                f'<button type="button" id="receipt-modal-close" class="btn-secondary btn-sm">{esc(t["close_modal"])}</button>'
                f"</div>"
                f'<img id="receipt-modal-img" src="" alt="Receipt">'
                f"</div></div>"
            )

            mini_js = asset_script("photo.js")
            body = f"{top_bar}{mini_header}{notes_section}{tracker_section}{lightbox_modal}{mini_js}"

        elif path == "/dienste":
            title = t["house_duties"]
            body = message_block("duties_intro", lang, can_manage_general, "/dienste")
            duty_cards = ""
            for r in db.execute("SELECT * FROM duties ORDER BY duty"):
                controls = (
                    form("remove-duty", f'<input type="hidden" name="id" value="{r[0]}">' + button(t["remove_entry"]))
                    if can_manage_general
                    else ""
                )
                duty_cards += panel(esc(r[1]), f"<p>{esc(r[2])}</p>" + controls)
            body += duty_cards or f"<p>{t['no_duties']}</p>"
            if can_manage_general:
                body += panel(
                    t["add_duty"],
                    form("duty", field(t["duty_label"], "duty") + field(t["contact_label"], "contact") + button(t["save"])),
                )

        elif path == "/admin" and can_manage_passwords:
            title = t["admin_settings"]
            body = panel(
                t["change_resident_pw"],
                form("rotate", field(t["new_shared_pw"], "password", "password", 'minlength="10"') + button(t["change_logout_residents"])),
            )
            body += panel(
                t["change_dienstleiter_pw"],
                form("rotate-dienstleiter", field(t["new_shared_pw"], "password", "password", 'minlength="10"') + button(t["change_logout_dienstleiter"])),
            )
            body += panel(
                t["your_pw"],
                form(
                    "password",
                    field(t["current_pw"], "old", "password")
                    + field(t["new_pw"], "password", "password", 'minlength="10"')
                    + button(t["change_pw"]),
                ),
            )
            if role == "leitung":
                body += panel(
                    t["create_admin"],
                    form(
                        "add",
                        field(t["account_name"], "name")
                        + field(t["initial_pw"], "password", "password", 'minlength="10"')
                        + button(t["create_account"]),
                    ),
                )
                for r in db.execute("SELECT name FROM accounts WHERE name NOT IN ('leitung','residents','dienstleiter')"):
                    body += form(
                        "revoke",
                        f'<input type="hidden" name="name" value="{esc(r[0])}">'
                        + button(esc(r[0]) + f" · {t['revoke_access']}"),
                    )
        else:
            return self.reply(t["page_unavailable"], 404)

        is_detail = (
            (path.startswith("/objekte/") and path[9:].isdigit())
            or (path.startswith("/gast/") and path[6:].isdigit())
            or (path.startswith("/aktivitaeten/") and path[14:].isdigit())
            or (path.startswith("/mini/") and path[6:].isdigit())
        )
        self.reply(page(title, body, nav, lang=lang, current_path=full_current_path, compact_title=is_detail,
                        level=level, topnav=links, tabbar=tabbar))

    def _post(self):
        lang = self.lang()
        t = TEXT[lang]
        data = parse_qs(self._raw_body.decode("utf-8", "replace"))

        def val(k, limit=200):
            s = data.get(k, [""])[0].strip()
            if not s or len(s) > limit:
                raise ValueError(k)
            return s

        path = self.path
        role = self.role()
        level = self.access_level()
        is_admin = level == "admin"
        can_manage_general = level in ("admin", "dienstleiter")
        can_manage_guestroom = is_admin
        can_manage_rules_news = is_admin
        can_manage_passwords = is_admin

        try:
            if path == "/login":
                ip = self.client_ip()
                wait = login_wait(ip)
                if wait:
                    return self.reply(
                        page(t["login_failed"], "<p>" + t["too_many_logins"].format(mins=(wait + 59) // 60) + "</p>",
                             lang=lang, current_path="/"),
                        429,
                    )
                name = val("account")
                if not valid(name, val("password")):
                    login_failed(ip)
                    return self.reply(
                        page(
                            t["login_failed"],
                            f'<a href="/">{t["back_residents"]}</a> · '
                            f'<a href="/dienstleiter">{t["back_dienstleiter"]}</a> · '
                            f'<a href="/admin">{t["go_admin"]}</a>',
                            lang=lang,
                            current_path="/",
                        ),
                        403,
                    )
                login_succeeded(ip)
                token = secrets.token_urlsafe(32)
                ttl = session_ttl(name)
                sessions[token] = (name, time.time() + ttl)
                return self.redirect("/", token, max_age=ttl)
            if not role:
                return self.reply("", 403)
            if path == "/logout":
                tok = self.token()
                sessions.pop(tok, None)
                session_votes.pop(tok, None)
                return self.redirect("/", "")
            with db:
                if path == "/book":
                    room_id = int(val("room_id"))
                    room = db.execute("SELECT id, max_guests, price FROM rooms WHERE id=?", (room_id,)).fetchone()
                    if not room:
                        raise ValueError()
                    arrival = date.fromisoformat(val("arrival"))
                    departure = date.fromisoformat(val("departure"))
                    guests = int(val("guests"))
                    if arrival < date.today() or departure <= arrival or not 1 <= guests <= room["max_guests"]:
                        raise ValueError()
                    if room_has_conflict(room_id, arrival, departure):
                        raise ValueError("conflict")
                    notes = data.get("notes", [""])[0].strip()
                    if len(notes) > 1000 or len(notes.split()) > 100:
                        raise ValueError()
                    bedding = 1 if data.get("bedding", [""])[0] == "1" else 0
                    nights = (departure - arrival).days
                    total_price = nights * float(room["price"]) + (BEDDING_FEE if bedding else 0)
                    booking_status = "approved" if can_manage_guestroom else "pending"
                    approved_ts = int(time.time()) if can_manage_guestroom else 0
                    db.execute(
                        """INSERT INTO bookings(room_id, name, arrival, departure, guests, bedding, total_price, notes, status, approved_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (room_id, val("name", 80), arrival.isoformat(), departure.isoformat(), guests, bedding, total_price, notes, booking_status, approved_ts),
                    )
                    dest = "/gast" if can_manage_guestroom else "/danke"
                elif path == "/item":
                    tags_map = get_tags_map()
                    item_title = val("title", 120)
                    desc = data.get("description", [""])[0].strip()
                    if len(desc) > 1000 or len(desc.split()) > 100:
                        raise ValueError()
                    chosen_tags = [k for k in data.get("tags", []) if k in tags_map]
                    if not chosen_tags:
                        raise ValueError()
                    photo = base64.b64decode(val("image", 2000000), validate=True)
                    if not photo.startswith(b"\xff\xd8\xff") or not photo.endswith(b"\xff\xd9"):
                        raise ValueError()
                    thumb = None
                    thumb_raw = data.get("thumb", [""])[0].strip()
                    if thumb_raw and len(thumb_raw) <= 400000:
                        thumb = base64.b64decode(thumb_raw, validate=True)
                        if not (thumb.startswith(b"\xff\xd8\xff") and thumb.endswith(b"\xff\xd9")):
                            thumb = None
                    db.execute(
                        "INSERT INTO items(title,image,thumb,description,tags,created_at) VALUES (?,?,?,?,?,?)",
                        (item_title, photo, thumb, desc, ",".join(dict.fromkeys(chosen_tags)), int(time.time())),
                    )
                    dest = "/objekte"
                elif path == "/take-item":
                    db.execute("UPDATE items SET taken_at=? WHERE id=? AND taken_at=0", (int(time.time()), val("id")))
                    dest = "/objekte"
                elif path == "/restore-item":
                    db.execute("UPDATE items SET taken_at=0 WHERE id=?", (val("id"),))
                    dest = "/objekte"
                elif path == "/machine-start":
                    sync_machines()
                    m_id = int(val("id"))
                    m = db.execute("SELECT status FROM machines WHERE id=?", (m_id,)).fetchone()
                    if not m or m["status"] not in ("free", "pending"):
                        raise ValueError()
                    quick = data.get("dur", [""])[0].strip()
                    if quick:
                        # one-tap start: begins now (the server's clock) and runs for the chosen minutes
                        clock = datetime.now()
                        hh, mm = clock.hour, clock.minute
                        dur_h, dur_m = divmod(int(quick), 60)
                    else:
                        hh_str, mm_str = val("start_time", 10).split(":")
                        hh, mm = int(hh_str), int(mm_str)
                        if not (0 <= hh <= 23 and 0 <= mm <= 59):
                            raise ValueError("start_time")
                        dur_h = int(val("hours", 5))
                        dur_m = int(val("minutes", 5))
                    dur_secs = dur_h * 3600 + dur_m * 60
                    if not (0 <= dur_h <= 12 and 0 <= dur_m <= 59 and 60 <= dur_secs <= 12 * 3600):
                        raise ValueError()
                    now_dt = datetime.now().astimezone()
                    start_dt = now_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
                    start_ts = int(start_dt.timestamp())
                    now_ts = int(now_dt.timestamp())
                    if start_ts - now_ts > 12 * 3600:
                        start_ts -= 86400
                    elif now_ts - start_ts > 12 * 3600:
                        start_ts += 86400
                    end_ts = start_ts + dur_secs
                    if end_ts <= now_ts:
                        raise ValueError()
                    db.execute(
                        "UPDATE machines SET status='occupied', start_ts=?, end_ts=?, reserved=0, pending_until=0 WHERE id=?",
                        (start_ts, end_ts, m_id),
                    )
                    dest = "/wasch"
                elif path == "/machine-end":
                    sync_machines()
                    m_id = int(val("id"))
                    m = db.execute("SELECT status, reserved FROM machines WHERE id=?", (m_id,)).fetchone()
                    if not m or m["status"] != "occupied":
                        raise ValueError()
                    now_ts = int(time.time())
                    if m["reserved"]:
                        pend_end = now_ts + get_laundry_grace_mins() * 60
                        db.execute(
                            "UPDATE machines SET status='pending', start_ts=0, end_ts=0, reserved=0, pending_until=? WHERE id=?",
                            (pend_end, m_id),
                        )
                    else:
                        db.execute(
                            "UPDATE machines SET status='free', start_ts=0, end_ts=0, reserved=0, pending_until=0 WHERE id=?",
                            (m_id,),
                        )
                    dest = "/wasch"
                elif path == "/machine-reserve":
                    sync_machines()
                    m_id = int(val("id"))
                    m = db.execute("SELECT status, reserved FROM machines WHERE id=?", (m_id,)).fetchone()
                    if not m or m["status"] != "occupied" or m["reserved"]:
                        raise ValueError()
                    db.execute("UPDATE machines SET reserved=1 WHERE id=?", (m_id,))
                    dest = "/wasch"
                elif path == "/machine-unreserve":
                    sync_machines()
                    m_id = int(val("id"))
                    db.execute("UPDATE machines SET reserved=0 WHERE id=? AND status='occupied'", (m_id,))
                    dest = "/wasch"
                # --- Activities, Mini-Activities, Gallery, Notes & Tracker ---
                elif path == "/add-activity":
                    act_title = val("title", 100)
                    photo = base64.b64decode(val("image", 2000000), validate=True)
                    if not photo.startswith(b"\xff\xd8\xff") or not photo.endswith(b"\xff\xd9"):
                        raise ValueError()
                    db.execute(
                        "INSERT INTO activities(title, image, created_at) VALUES (?,?,?)",
                        (act_title, photo, int(time.time())),
                    )
                    dest = "/aktivitaeten"
                elif path == "/remove-activity":
                    delete_activity_cascade(int(val("id")))
                    dest = "/aktivitaeten"
                elif path == "/add-activity-photo":
                    act_id = int(val("activity_id"))
                    if not db.execute("SELECT 1 FROM activities WHERE id=?", (act_id,)).fetchone():
                        raise ValueError()
                    caption = data.get("caption", [""])[0].strip()
                    if len(caption) > 140:
                        raise ValueError()
                    photo = base64.b64decode(val("image", 2000000), validate=True)
                    if not photo.startswith(b"\xff\xd8\xff") or not photo.endswith(b"\xff\xd9"):
                        raise ValueError()
                    db.execute(
                        "INSERT INTO activity_photos(activity_id, image, caption, created_at) VALUES (?,?,?,?)",
                        (act_id, photo, caption, int(time.time())),
                    )
                    dest = f"/aktivitaeten/{act_id}"
                elif path == "/remove-activity-photo":
                    if not can_manage_general:
                        return self.reply("", 403)
                    photo_id = int(val("id"))
                    act_id = int(val("activity_id"))
                    db.execute("DELETE FROM activity_photos WHERE id=? AND activity_id=?", (photo_id, act_id))
                    dest = f"/aktivitaeten/{act_id}"
                elif path == "/add-mini-activity":
                    act_id = int(val("activity_id"))
                    if not db.execute("SELECT 1 FROM activities WHERE id=?", (act_id,)).fetchone():
                        raise ValueError()
                    m_title = val("title", 100)
                    max_p = int(val("max_participants", 10))
                    if not 1 <= max_p <= 200:
                        raise ValueError()
                    photo = base64.b64decode(val("image", 2000000), validate=True)
                    if not photo.startswith(b"\xff\xd8\xff") or not photo.endswith(b"\xff\xd9"):
                        raise ValueError()
                    now_ts = int(time.time())
                    cur = db.execute(
                        """INSERT INTO mini_activities(activity_id, title, max_participants, image, created_at)
                           VALUES (?,?,?,?,?)""",
                        (act_id, m_title, max_p, photo, now_ts),
                    )
                    new_mini_id = cur.lastrowid
                    db.execute(
                        "INSERT INTO mini_notes(mini_id, title, body, pinned, votes, created_at) VALUES (?,?,?,1,0,?)",
                        (new_mini_id, t["starter_note_title"], t["starter_note_body"], now_ts),
                    )
                    dest = f"/mini/{new_mini_id}"
                elif path == "/remove-mini-activity":
                    m_id = int(val("id"))
                    act_id = int(val("activity_id"))
                    delete_mini_cascade(m_id)
                    dest = f"/aktivitaeten/{act_id}"
                elif path == "/mini-note-add":
                    m_id = int(val("mini_id"))
                    if not db.execute("SELECT 1 FROM mini_activities WHERE id=?", (m_id,)).fetchone():
                        raise ValueError()
                    n_title = data.get("title", [""])[0].strip()
                    if len(n_title) > 120:
                        raise ValueError()
                    n_body = val("body", 2000)
                    n_pin = 1 if data.get("pinned", ["0"])[0] == "1" else 0
                    db.execute(
                        "INSERT INTO mini_notes(mini_id, title, body, pinned, votes, created_at) VALUES (?,?,?,?,0,?)",
                        (m_id, n_title, n_body, n_pin, int(time.time())),
                    )
                    dest = f"/mini/{m_id}"
                elif path == "/mini-note-edit":
                    n_id = int(val("id"))
                    m_id = int(val("mini_id"))
                    n_title = data.get("title", [""])[0].strip()
                    if len(n_title) > 120:
                        raise ValueError()
                    n_body = val("body", 2000)
                    db.execute(
                        "UPDATE mini_notes SET title=?, body=? WHERE id=? AND mini_id=?",
                        (n_title, n_body, n_id, m_id),
                    )
                    dest = f"/mini/{m_id}"
                elif path == "/mini-note-pin":
                    n_id = int(val("id"))
                    m_id = int(val("mini_id"))
                    db.execute(
                        "UPDATE mini_notes SET pinned = CASE WHEN pinned=1 THEN 0 ELSE 1 END WHERE id=? AND mini_id=?",
                        (n_id, m_id),
                    )
                    dest = f"/mini/{m_id}"
                elif path == "/mini-note-vote":
                    n_id = int(val("id"))
                    m_id = int(val("mini_id"))
                    voted = session_votes.setdefault(self.token(), set())
                    if n_id in voted:
                        voted.remove(n_id)
                        db.execute("UPDATE mini_notes SET votes = MAX(0, votes - 1) WHERE id=? AND mini_id=?", (n_id, m_id))
                    else:
                        voted.add(n_id)
                        db.execute("UPDATE mini_notes SET votes = votes + 1 WHERE id=? AND mini_id=?", (n_id, m_id))
                    dest = f"/mini/{m_id}"
                elif path == "/mini-note-delete":
                    n_id = int(val("id"))
                    m_id = int(val("mini_id"))
                    db.execute("DELETE FROM mini_notes WHERE id=? AND mini_id=?", (n_id, m_id))
                    dest = f"/mini/{m_id}"
                elif path == "/mini-budget-settings":
                    m_id = int(val("mini_id"))
                    contrib = parse_eur(data.get("contribution", ["0"])[0])
                    start_bal = parse_eur(data.get("starting_balance", ["0"])[0])
                    db.execute(
                        "UPDATE mini_activities SET contribution=?, starting_balance=? WHERE id=?",
                        (contrib, start_bal, m_id),
                    )
                    dest = f"/mini/{m_id}"
                elif path == "/mini-member-add":
                    m_id = int(val("mini_id"))
                    if not db.execute("SELECT 1 FROM mini_activities WHERE id=?", (m_id,)).fetchone():
                        raise ValueError()
                    mem_name = val("name", 60)
                    db.execute(
                        "INSERT INTO mini_members(mini_id, name, paid, paid_date) VALUES (?,?,0,'')",
                        (m_id, mem_name),
                    )
                    dest = f"/mini/{m_id}"
                elif path == "/mini-member-toggle":
                    mem_id = int(val("id"))
                    m_id = int(val("mini_id"))
                    row = db.execute("SELECT paid FROM mini_members WHERE id=? AND mini_id=?", (mem_id, m_id)).fetchone()
                    if not row:
                        raise ValueError()
                    next_paid = 0 if row["paid"] else 1
                    paid_dt = date.today().strftime("%d/%m/%Y") if next_paid else ""
                    db.execute(
                        "UPDATE mini_members SET paid=?, paid_date=? WHERE id=? AND mini_id=?",
                        (next_paid, paid_dt, mem_id, m_id),
                    )
                    dest = f"/mini/{m_id}"
                elif path == "/mini-member-remove":
                    mem_id = int(val("id"))
                    m_id = int(val("mini_id"))
                    db.execute("DELETE FROM mini_members WHERE id=? AND mini_id=?", (mem_id, m_id))
                    dest = f"/mini/{m_id}"
                elif path == "/mini-log-add":
                    m_id = int(val("mini_id"))
                    if not db.execute("SELECT 1 FROM mini_activities WHERE id=?", (m_id,)).fetchone():
                        raise ValueError()
                    log_date = val("log_date", 20)
                    person = val("person", 80)
                    took_out = parse_eur(data.get("took_out", ["0"])[0])
                    put_back = parse_eur(data.get("put_back", ["0"])[0])
                    if took_out == 0 and put_back == 0:
                        raise ValueError()
                    raw_items = data.get("items_json", ["[]"])[0]
                    if len(raw_items) > 10000:
                        raise ValueError()
                    parsed_items = json.loads(raw_items)
                    clean_items = []
                    for it in parsed_items[:40]:
                        nm = str(it.get("name", "")).strip()[:80]
                        amt = parse_eur(it.get("amount", 0))
                        if nm or amt > 0:
                            clean_items.append({"name": nm, "amount": amt})

                    raw_receipts = data.get("receipts_json", ["[]"])[0]
                    if len(raw_receipts) > 8500000:
                        raise ValueError()
                    receipts_b64 = json.loads(raw_receipts)
                    decoded_receipts = []
                    for b64_str in receipts_b64[:4]:
                        img_bytes = base64.b64decode(str(b64_str), validate=True)
                        if not img_bytes.startswith(b"\xff\xd8\xff") or not img_bytes.endswith(b"\xff\xd9"):
                            raise ValueError()
                        decoded_receipts.append(img_bytes)

                    cur = db.execute(
                        """INSERT INTO mini_logs(mini_id, log_date, person, took_out, put_back, items_json, created_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (m_id, log_date, person, took_out, put_back, json.dumps(clean_items), int(time.time())),
                    )
                    new_log_id = cur.lastrowid
                    for img_bytes in decoded_receipts:
                        db.execute("INSERT INTO mini_log_photos(log_id, image) VALUES (?,?)", (new_log_id, img_bytes))
                    dest = f"/mini/{m_id}"
                elif path == "/mini-log-delete":
                    lg_id = int(val("id"))
                    m_id = int(val("mini_id"))
                    db.execute("DELETE FROM mini_log_photos WHERE log_id=?", (lg_id,))
                    db.execute("DELETE FROM mini_logs WHERE id=? AND mini_id=?", (lg_id, m_id))
                    dest = f"/mini/{m_id}"

                # --- Editable Settings (Scoped by Sector) ---
                elif path == "/save-setting":
                    key = val("key", 60)
                    if key not in DEFAULT_SETTINGS:
                        raise ValueError()
                    if key in ADMIN_ONLY_SETTINGS and not is_admin:
                        return self.reply("", 403)
                    if key in GENERAL_SETTINGS and not can_manage_general:
                        return self.reply("", 403)
                    if key == "shelf_max_days":
                        days_val = int(val("en", 10))
                        if not 1 <= days_val <= 3650:
                            raise ValueError()
                        en_text = de_text = str(days_val)
                    elif key == "laundry_grace_mins":
                        mins_val = int(val("en", 10))
                        if not 1 <= mins_val <= 120:
                            raise ValueError()
                        en_text = de_text = str(mins_val)
                    else:
                        en_text = data.get("en", [""])[0].strip()
                        de_text = data.get("de", [""])[0].strip()
                        if len(en_text) > 2000 or len(de_text) > 2000:
                            raise ValueError()
                    db.execute("INSERT OR REPLACE INTO settings(key, en, de) VALUES (?,?,?)", (key, en_text, de_text))
                    next_dest = data.get("next", ["/"])[0]
                    dest = next_dest if next_dest.startswith("/") and not next_dest.startswith("//") else "/"

                # --- General Management Sectors (Admin + Dienstleiter) ---
                elif path in ("/add-tag", "/remove-tag", "/add-machine", "/edit-machine", "/remove-machine", "/remove-duty", "/duty"):
                    if not can_manage_general:
                        return self.reply("", 403)
                    if path == "/add-tag":
                        en_label = val("en", 50)
                        de_label = val("de", 50)
                        base_slug = re.sub(r"[^a-z0-9]+", "-", en_label.lower()).strip("-") or "tag"
                        slug = base_slug
                        idx = 2
                        while db.execute("SELECT 1 FROM tags WHERE key=?", (slug,)).fetchone():
                            slug = f"{base_slug}-{idx}"
                            idx += 1
                        db.execute("INSERT INTO tags(key, en, de) VALUES (?,?,?)", (slug, en_label, de_label))
                        dest = "/objekte"
                    elif path == "/remove-tag":
                        db.execute("DELETE FROM tags WHERE key=?", (val("key", 60),))
                        dest = "/objekte"
                    elif path == "/add-machine":
                        m_name = val("name", 60)
                        m_kind = val("kind", 20)
                        if m_kind not in ("washer", "dryer"):
                            raise ValueError()
                        db.execute("INSERT INTO machines(name, kind) VALUES (?,?)", (m_name, m_kind))
                        dest = "/wasch"
                    elif path == "/edit-machine":
                        m_id = int(val("id"))
                        m_name = val("name", 60)
                        m_kind = val("kind", 20)
                        if m_kind not in ("washer", "dryer"):
                            raise ValueError()
                        db.execute("UPDATE machines SET name=?, kind=? WHERE id=?", (m_name, m_kind, m_id))
                        dest = "/wasch"
                    elif path == "/remove-machine":
                        db.execute("DELETE FROM machines WHERE id=?", (int(val("id")),))
                        dest = "/wasch"
                    elif path == "/remove-duty":
                        db.execute("DELETE FROM duties WHERE id=?", (val("id"),))
                        dest = "/dienste"
                    elif path == "/duty":
                        db.execute("INSERT INTO duties(duty,contact) VALUES (?,?)", (val("duty"), val("contact")))
                        dest = "/dienste"

                # --- Guest Room Administration (Full Admin Only) ---
                elif path in ("/booking-payment", "/approve-booking", "/reject-booking", "/add-room", "/edit-room", "/remove-room"):
                    if not can_manage_guestroom:
                        return self.reply("", 403)
                    if path == "/booking-payment":
                        booking_id = int(val("id"))
                        payment = val("paid")
                        if payment not in ("0", "1"):
                            raise ValueError()
                        updated = db.execute(
                            "UPDATE bookings SET paid=? WHERE id=? AND status='approved'",
                            (int(payment), booking_id),
                        )
                        if not updated.rowcount:
                            return self.reply(t["no_approved"], 404)
                        sort = "latest" if data.get("sort") == ["latest"] else "soonest"
                        dest = f"/gast?sort={sort}#booking-{booking_id}"
                    elif path == "/approve-booking":
                        db.execute("UPDATE bookings SET status='approved', approved_at=? WHERE id=?", (int(time.time()), val("id")))
                        dest = "/gast"
                    elif path == "/reject-booking":
                        db.execute("DELETE FROM bookings WHERE id=?", (val("id"),))
                        sort_param = data.get("sort", [""])[0]
                        dest = f"/gast?sort={sort_param}" if sort_param in ("soonest", "latest") else "/gast"
                    elif path == "/add-room":
                        floor = val("floor", 100)
                        max_guests = int(val("max_guests"))
                        price = float(val("price"))
                        if not 1 <= max_guests <= 20 or price < 0:
                            raise ValueError()
                        photo = base64.b64decode(val("image", 2000000), validate=True)
                        if not photo.startswith(b"\xff\xd8\xff") or not photo.endswith(b"\xff\xd9"):
                            raise ValueError()
                        db.execute(
                            "INSERT INTO rooms(floor, max_guests, price, image) VALUES (?,?,?,?)",
                            (floor, max_guests, price, photo),
                        )
                        dest = "/gast"
                    elif path == "/edit-room":
                        room_id = int(val("id"))
                        floor = val("floor", 100)
                        max_guests = int(val("max_guests"))
                        price = float(val("price"))
                        if not 1 <= max_guests <= 20 or price < 0:
                            raise ValueError()
                        raw_img = data.get("image", [""])[0].strip()
                        if raw_img:
                            photo = base64.b64decode(raw_img, validate=True)
                            if not photo.startswith(b"\xff\xd8\xff") or not photo.endswith(b"\xff\xd9"):
                                raise ValueError()
                            db.execute(
                                "UPDATE rooms SET floor=?, max_guests=?, price=?, image=? WHERE id=?",
                                (floor, max_guests, price, photo, room_id),
                            )
                        else:
                            db.execute(
                                "UPDATE rooms SET floor=?, max_guests=?, price=? WHERE id=?",
                                (floor, max_guests, price, room_id),
                            )
                        dest = "/gast"
                    elif path == "/remove-room":
                        room_id = int(val("id"))
                        db.execute("DELETE FROM bookings WHERE room_id=?", (room_id,))
                        db.execute("DELETE FROM rooms WHERE id=?", (room_id,))
                        dest = "/gast"

                # --- Rules & News Administration (Full Admin Only) ---
                elif path == "/announcement-vote":
                    key = int(val("id"))
                    votes = session_votes.setdefault(self.token(), set())
                    marker = ("announcement", key)
                    delta = -1 if marker in votes else 1
                    updated = db.execute("UPDATE announcements SET votes=MAX(0,votes+?) WHERE id=?", (delta, key))
                    if not updated.rowcount:
                        raise ValueError()
                    if delta == 1:
                        votes.add(marker)
                    else:
                        votes.remove(marker)
                    dest = "/regeln"
                elif path in ("/announcement-edit", "/add-announcement", "/toggle-pin-announcement", "/remove-announcement", "/upload-rules-pdf", "/remove-rules-pdf"):
                    if not can_manage_rules_news:
                        return self.reply("", 403)
                    if path == "/announcement-edit":
                        db.execute("UPDATE announcements SET title=?,body=? WHERE id=?",
                                   (val("title", 120), val("body", 2000), int(val("id"))))
                        dest = "/regeln"
                    elif path == "/add-announcement":
                        a_title = val("title", 120)
                        a_body = val("body", 2000)
                        a_pin = 1 if data.get("pinned", ["0"])[0] == "1" else 0
                        db.execute(
                            "INSERT INTO announcements(title, body, pinned, created_at) VALUES (?,?,?,?)",
                            (a_title, a_body, a_pin, int(time.time())),
                        )
                        dest = "/regeln"
                    elif path == "/toggle-pin-announcement":
                        a_id = int(val("id"))
                        db.execute("UPDATE announcements SET pinned = CASE WHEN pinned=1 THEN 0 ELSE 1 END WHERE id=?", (a_id,))
                        dest = "/regeln"
                    elif path == "/remove-announcement":
                        db.execute("DELETE FROM announcements WHERE id=?", (int(val("id")),))
                        dest = "/regeln"
                    elif path == "/upload-rules-pdf":
                        fname = val("filename", 140)
                        pdf_bytes = base64.b64decode(val("pdf_b64", 11500000), validate=True)
                        if not pdf_bytes.startswith(b"%PDF-"):
                            raise ValueError()
                        db.execute(
                            "INSERT OR REPLACE INTO documents(key, filename, data, updated_at) VALUES ('house_rules', ?, ?, ?)",
                            (fname, pdf_bytes, int(time.time())),
                        )
                        dest = "/regeln"
                    elif path == "/remove-rules-pdf":
                        db.execute("DELETE FROM documents WHERE key='house_rules'")
                        dest = "/regeln"

                # --- Passwords & Access Administration (Full Admin Only) ---
                elif path in ("/rotate", "/rotate-dienstleiter", "/password", "/add", "/revoke"):
                    if not can_manage_passwords:
                        return self.reply("", 403)
                    if path == "/rotate":
                        target = "residents"
                    elif path == "/rotate-dienstleiter":
                        target = "dienstleiter"
                    else:
                        target = role
                    if path in ("/add", "/revoke"):
                        if role != "leitung":
                            return self.reply("", 403)
                        target = val("name", 50)
                        if target in ("residents", "dienstleiter", "leitung"):
                            raise ValueError()
                    if path == "/revoke":
                        db.execute("DELETE FROM accounts WHERE name=?", (target,))
                    else:
                        if path == "/password" and not valid(role, val("old")):
                            raise ValueError()
                        if path == "/add" and db.execute("SELECT 1 FROM accounts WHERE name=?", (target,)).fetchone():
                            raise ValueError()
                        password = val("password")
                        if len(password) < 10:
                            raise ValueError()
                        setpass(target, password)
                    sessions.revoke_account(target)
                    dest = "/admin"
                else:
                    return self.reply("", 404)
            self.redirect(dest)
        except (ValueError, OverflowError) as err:
            reason = str(err.args[0]) if err.args else ""
            if reason == "conflict":
                message = t["check_input_conflict"]
            elif reason in FIELD_LABELS:
                message = t["check_input_field"].format(fields=FIELD_LABELS[reason][1 if lang == "de" else 0]) + " " + t["check_input_body"]
            else:
                message = t["check_input_body"]
            self.reply(
                page(
                    t["check_input"],
                    f'<p>{esc(message)}</p><p class="error-actions">'
                    f'<button type="button" class="js-back">{t["back_to_form"]}</button> '
                    f'<a href="/">{t["back_overview"]}</a></p>',
                    lang=lang,
                    current_path=path,
                ),
                400,
            )


def lan_address():
    """The address of this PC on the local network (what a phone on the same Wi-Fi has to type)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))  # no packet is sent
        return probe.getsockname()[0]
    except OSError:
        pass
    finally:
        probe.close()
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return ""


def make_backup(keep=10, min_age_hours=6):
    """Copy the database into backups/ at start-up (at most every few hours, newest 10 kept)."""
    BACKUPS.mkdir(exist_ok=True)
    existing = sorted(BACKUPS.glob("demo-*.sqlite3"))
    if existing and time.time() - existing[-1].stat().st_mtime < min_age_hours * 3600:
        return
    target = BACKUPS / datetime.now().strftime("demo-%Y%m%d-%H%M%S.sqlite3")
    copy = sqlite3.connect(target)
    try:
        db.backup(copy)
    finally:
        copy.close()
    for old in sorted(BACKUPS.glob("demo-*.sqlite3"))[:-keep]:
        old.unlink()


def main():
    global PORT, BIND, LAN_MODE, ORIGIN, SECURE_COOKIES, TRUST_PROXY
    parser = argparse.ArgumentParser(description="Dormitory portal (local server)")
    parser.add_argument("--lan", action="store_true",
                        help="also accept visitors from your local network (e.g. your phone on the same Wi-Fi)")
    parser.add_argument("--port", type=int, default=PORT, help="port to listen on (default %(default)s)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    parser.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                        help="extra host name the site is reached under, e.g. portal.example.org (repeatable)")
    parser.add_argument("--secure-cookies", action="store_true", help="mark cookies Secure (when served over https)")
    parser.add_argument("--trust-proxy", action="store_true", help="read visitor addresses from X-Forwarded-For")
    args = parser.parse_args()
    PORT = args.port
    LAN_MODE = args.lan
    BIND = "0.0.0.0" if args.lan else "127.0.0.1"
    SECURE_COOKIES = SECURE_COOKIES or args.secure_cookies
    TRUST_PROXY = TRUST_PROXY or args.trust_proxy
    EXTRA_HOSTS.update(h.lower() for h in args.allow_host)
    ORIGIN = "http://127.0.0.1:%d" % PORT
    try:
        make_backup()
    except Exception as exc:  # a failed backup must never stop the portal from starting
        print("Backup skipped:", exc, flush=True)
    server = ThreadingHTTPServer((BIND, PORT), Portal)
    server.daemon_threads = True
    print("Portal running:  ", ORIGIN, flush=True)
    if args.lan:
        ip = lan_address()
        if ip:
            print("On your phone:   http://%s:%d   (same Wi-Fi as this PC)" % (ip, PORT), flush=True)
        print("Allow Python through the firewall if Windows asks. Stop with Ctrl+C.", flush=True)
    if not args.no_browser:
        webbrowser.open(ORIGIN)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)


if __name__ == "__main__":
    main()

