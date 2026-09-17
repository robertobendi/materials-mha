"""Small API client. MC_API_BASE picks mock or real; MC_TOKEN is real-only."""
import json, os, urllib.parse, urllib.request

BASE = os.environ.get("MC_API_BASE", "http://127.0.0.1:8899")
TOKEN = os.environ.get("MC_TOKEN")
READ_ONLY = os.environ.get("MC_READ_ONLY", "1") == "1"


def _req(method, path, body=None):
    url = BASE.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Accept", "application/json")
    if data:
        r.add_header("Content-Type", "application/json")
    if TOKEN:
        r.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(r, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def open_requests():
    query = ("receiver.community:30074283-2e48-4526-b2df-c3c2191db3cb "
             "AND type:community-submission AND is_open:true")
    return _req("GET", "/api/requests/?q=" + urllib.parse.quote(query))["hits"]["hits"]


def draft(record_id):
    return _req("GET", f"/api/records/{record_id}/draft")


def files(record_id):
    return _req("GET", f"/api/records/{record_id}/draft/files")["entries"]


def comment(request_id, text):
    if READ_ONLY:
        raise SystemExit("MC_READ_ONLY=1, refusing to write. Set MC_READ_ONLY=0 to post.")
    return _req("POST", f"/api/requests/{request_id}/comments", {"payload": {"content": text, "format": "html"}})


# No accept() or decline() here on purpose. Publishing is irreversible even for
# staff, so that click always happens by hand in the web UI.
