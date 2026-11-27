"""Run the portal on a web host that speaks WSGI, such as PythonAnywhere.

On your own PC you still start the portal with `py portal.py`; this file is only for the web host.
It passes each web request to the same code that portal.py uses, so both behave identically.
See SETUP-PYTHONANYWHERE.md for the steps.
"""
import http.client
import io
import os
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# The host serves the site over https and sits behind its own proxy.
os.environ.setdefault("PORTAL_SECURE_COOKIES", "1")
os.environ.setdefault("PORTAL_TRUST_PROXY", "1")

import portal  # noqa: E402  (creates/upgrades the database on first import)

# The host only forwards requests for this site's own address, so the host-name check of the
# stand-alone server is not needed here. The Origin check that protects forms stays active.
portal.host_allowed = lambda value: bool(value)

MAX_BODY = 12_000_000
_backup_lock = threading.Lock()
_last_backup_try = 0.0


def _maybe_backup():
    """Copy the database into backups/ now and then (portal.make_backup keeps the newest 10)."""
    global _last_backup_try
    if time.time() - _last_backup_try < 3600:
        return
    with _backup_lock:
        if time.time() - _last_backup_try < 3600:
            return
        _last_backup_try = time.time()
        try:
            with portal.DB_LOCK:
                portal.make_backup()
        except Exception:
            traceback.print_exc()


class _Request(portal.Portal):
    """The portal's request handler, fed from a WSGI request instead of a network socket."""

    def __init__(self, environ):  # deliberately does not call the socket-based base __init__
        self.command = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("REQUEST_URI") or environ.get("RAW_URI")
        if not path:
            path = environ.get("PATH_INFO", "/") or "/"
            if environ.get("QUERY_STRING"):
                path += "?" + environ["QUERY_STRING"]
        self.path = path
        self.request_version = "HTTP/1.1"
        self.requestline = f"{self.command} {self.path} HTTP/1.1"
        self.close_connection = True
        self.client_address = (environ.get("REMOTE_ADDR", ""), 0)

        headers = http.client.HTTPMessage()
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                headers[key[5:].replace("_", "-").title()] = value
        if environ.get("CONTENT_TYPE"):
            headers["Content-Type"] = environ["CONTENT_TYPE"]
        length = environ.get("CONTENT_LENGTH") or "0"
        headers["Content-Length"] = length
        self.headers = headers

        try:
            size = int(length)
        except ValueError:
            size = 0
        body = environ["wsgi.input"].read(size) if 0 < size <= MAX_BODY else b""
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()


def application(environ, start_response):
    _maybe_backup()
    req = _Request(environ)
    try:
        if req.command == "POST":
            req.do_POST()
        elif req.command in ("GET", "HEAD"):
            req.do_GET()
        else:
            start_response("405 Method Not Allowed", [("Content-Length", "0")])
            return [b""]
    except Exception:
        traceback.print_exc()

    raw = req.wfile.getvalue()
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        start_response("500 Internal Server Error", [("Content-Type", "text/plain"), ("Content-Length", "0")])
        return [b""]
    lines = head.decode("latin-1").split("\r\n")
    status = lines[0].split(" ", 1)[1] if " " in lines[0] else "500 Internal Server Error"
    hop_by_hop = {"connection", "keep-alive", "transfer-encoding", "server"}
    out_headers = []
    for line in lines[1:]:
        name, _, value = line.partition(":")
        if name and name.strip().lower() not in hop_by_hop:
            out_headers.append((name.strip(), value.strip()))
    start_response(status, out_headers)
    return [b"" if req.command == "HEAD" else body]
