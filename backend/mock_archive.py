#!/usr/bin/env python3
"""Mock InvenioRDM / Materials Cloud Archive.

Speaks the same URL shapes as the real archive.materialscloud.org API, so the
moderation scripts can be pointed at either one via MC_API_BASE.

    python3 backend/mock_archive.py            # serves on http://127.0.0.1:8899

State lives in backend/state.json. Delete it to reset.
"""
import json, os, re, uuid, datetime, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load():
    if not os.path.exists(STATE):
        return {"records": {}, "requests": {}}
    with open(STATE, encoding="utf-8") as f:
        return json.load(f)


def save(s):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)


def create_submission(record, mcid=None):
    """Used by submit_test_record.py, and by POST /api/mock/submit."""
    s = load()
    rid = record.get("id") or uuid.uuid4().hex[:5] + "-" + uuid.uuid4().hex[:5]
    record["id"] = rid
    record["created"] = record.get("created", now())
    record["is_published"] = False
    record["is_draft"] = True
    record["status"] = "submitted"
    record.setdefault("pids", {}).setdefault("mcid", {"identifier": mcid or "2026.TEST", "provider": "mcid"})
    req_id = uuid.uuid4().hex[:8]
    s["records"][rid] = record
    s["requests"][req_id] = {
        "id": req_id,
        "type": "community-submission",
        "status": "submitted",
        "is_open": True,
        "created": now(),
        "updated": now(),
        "title": record["metadata"]["title"],
        "topic": {"record": rid},
        "created_by": {"user": "submitter"},
        "receiver": {"community": "30074283-2e48-4526-b2df-c3c2191db3cb"},
        "comments": [],
        "links": {"self": f"/api/requests/{req_id}"},
    }
    save(s)
    return req_id, rid


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        s = load()
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/me":
            return self._send(200, {"id": "moderator", "email": "roberto.bendinelli@example.org",
                                    "roles": []})

        if path in ("/api/requests", "/api/requests/"):
            hits = list(s["requests"].values())
            term = query.get("q", [""])[0]
            for status in ("submitted", "accepted", "declined"):
                if f"status:{status}" in term:
                    hits = [request for request in hits if request.get("status") == status]
            if "is_open:true" in term or query.get("is_open") == ["true"]:
                hits = [request for request in hits if request.get("is_open")]
            reverse = query.get("sort", ["newest"])[0] != "oldest"
            hits.sort(key=lambda request: request.get("updated") or request.get("created") or "",
                      reverse=reverse)
            total = len(hits)
            size = min(int(query.get("size", [10])[0]), 100)
            page = max(int(query.get("page", [1])[0]), 1)
            hits = hits[(page - 1) * size:page * size]
            return self._send(200, {"hits": {"hits": hits, "total": total}})

        m = re.match(r"^/api/requests/([^/]+)/timeline$", path)
        if m:
            req = s["requests"].get(m.group(1))
            if not req:
                return self._send(404, {"message": "not found"})
            events = req.get("comments", [])
            return self._send(200, {"hits": {"hits": events, "total": len(events)},
                                    "sortBy": "oldest"})

        m = re.match(r"^/api/records/([^/]+)/draft/files$", path)
        if m:
            rec = s["records"].get(m.group(1))
            if not rec:
                return self._send(404, {"message": "not found"})
            entries = rec["files"]["entries"]
            return self._send(200, {"entries": [dict(v, key=k) for k, v in entries.items()]})

        m = re.match(r"^/api/records/([^/]+)(/draft)?$", path)
        if m:
            rec = s["records"].get(m.group(1))
            return self._send(200, rec) if rec else self._send(404, {"message": "not found"})

        m = re.match(r"^/api/requests/([^/]+)$", path)
        if m:
            req = s["requests"].get(m.group(1))
            return self._send(200, req) if req else self._send(404, {"message": "not found"})

        self._send(404, {"message": "no route"})

    def do_POST(self):
        s = load()
        path = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")

        if path == "/api/mock/submit":
            req_id, rid = create_submission(body)
            return self._send(201, {"request_id": req_id, "record_id": rid})

        m = re.match(r"^/api/requests/([^/]+)/comments$", path)
        if m:
            req = s["requests"].get(m.group(1))
            if not req:
                return self._send(404, {"message": "not found"})
            c = {"id": uuid.uuid4().hex[:8], "created": now(),
                 "updated": now(), "type": "C", "created_by": {"user": "moderator"},
                 "payload": {"content": body.get("payload", {}).get(
                     "content", body.get("content", "")), "format": "html"}}
            req["comments"].append(c)
            req["updated"] = now()
            save(s)
            return self._send(201, c)

        self._send(404, {"message": "no route"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    print(f"mock archive on http://127.0.0.1:{port}  (state: {STATE})")
    HTTPServer(("127.0.0.1", port), H).serve_forever()
