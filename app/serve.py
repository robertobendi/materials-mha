#!/usr/bin/env python3
"""Local moderation workspace: advisory review plus confirmed comment sending."""
import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import deterministic
import ai_review
import dashboard as dashboard_data
from archive_client import Archive, CommentWriter, IDENTIFIER, PRODUCTION

ARCHIVE = os.environ.get("MC_ARCHIVE", PRODUCTION).rstrip("/")
HOST = (os.environ.get("MC_HOST", "127.0.0.1").strip() or "127.0.0.1")
PORT = int(os.environ.get("PORT", 8000))
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
HOSTED = HOST not in LOOPBACK
ALLOWED_HOSTS = {value.strip().lower()
                 for value in os.environ.get("MC_ALLOWED_HOSTS", "").split(",") if value.strip()}
ALLOW_ENV_TOKEN = os.environ.get("MC_ALLOW_ENV_TOKEN", "").strip().lower() in ("1", "true", "yes")
SESSION_TTL = 3600
REVIEW_TTL = 900
MAX_BODY = 2 * 1024 * 1024
DASHBOARD_TTL = 60
MAX_REPLY_CHARS = 20_000
COMMENTS_ENABLED = os.environ.get("MC_DISABLE_COMMENTS", "").strip().lower() not in (
    "1", "true", "yes",
)
SESSIONS = {}
LOCK = threading.RLock()
STARTUP_TOKEN = ""
PLAYBOOK = dashboard_data.load_playbook(ROOT)
AI_DEFAULT = None
try:
    AI_DEFAULT = ai_review.default_connection(os.environ)
except ValueError as error:
    print("Ignoring invalid MC_AI_* configuration:", error, file=sys.stderr)


def local_token():
    """Load MC_TOKEN from the process or the ignored repo-local .env file."""
    token = os.environ.get("MC_TOKEN", "")
    if not token:
        path = os.path.join(ROOT, ".env")
        try:
            with open(path, encoding="utf-8") as source:
                for line in source:
                    if line.startswith("MC_TOKEN="):
                        token = line[len("MC_TOKEN="):].strip()
                        break
        except FileNotFoundError:
            return ""
    if not token or len(token) > 4096 or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise ValueError("The local MC_TOKEN entry is invalid.")
    return token


def startup_message(url):
    """Return a console banner that is safe in Windows' legacy code pages."""
    mode = "comments only; decisions disabled" if COMMENTS_ENABLED else "read-only"
    return f"Materials Cloud Review -> {url}  ({mode})"

def entries_of(rec):
    ent = rec.get("files", {}).get("entries", {})
    if isinstance(ent, dict):
        return [dict(v, key=k) for k, v in ent.items()]
    return ent


def verdict(rec):
    res = deterministic.check(rec, entries_of(rec))
    hard = [f for f in res["findings"] if f["key"] != "REVIEW"]
    soft = [f for f in res["findings"] if f["key"] == "REVIEW"]
    size = any(f["key"] == "SIZE" for f in hard)

    if size and res["funding_waiver"]:
        status, headline = "warn", "Over the size limit, but the funder covers it"
        note = "Funded by " + ", ".join(x.strip() for x in res["funders"] if x.strip()) + \
               ", a human must confirm the funding exception and all other findings."
    elif size:
        status, headline = "stop", "Over the size limit, with no covered funder"
        note = "Ask them to shrink it, or escalate before deciding."
    elif hard:
        status, headline = "changes", f"{len(hard)} finding{'s' if len(hard) > 1 else ''} to review"
        note = "Verify the evidence before copying the draft feedback. Nothing has been sent."
    elif soft:
        status = "warn"
        headline = f"No automatic changes, {len(soft)} human check{'s' if len(soft) > 1 else ''}"
        note = "Resolve the review questions before deciding whether feedback is needed."
    else:
        status, headline = "ok", "Automated checks passed"
        note = "Automated checks passed. Scope, description, and file contents still need your review."

    seen, msg = set(), []
    for f in hard:
        if f["key"] != "SIZE" and f["text"] not in seen:
            seen.add(f["text"])
            msg.append(f["text"])

    return {"status": status, "headline": headline, "note": note,
            "title": rec.get("metadata", {}).get("title", "(untitled)"),
            "gb": round(res["total_bytes"] / 1e9, 2), "aiida": res["is_aiida"],
            "waiver": res["funding_waiver"], "funders": [f.strip() for f in res["funders"] if f.strip()],
            "affiliation_rors": res["affiliation_rors"],
            "findings": [{"key": f["key"], "detail": f.get("detail") or "", "text": f["text"]} for f in hard],
            "eyeball": [f["text"].replace("Human check: ", "") for f in soft],
            "message": "\n".join(msg)}



def fingerprint(record, context):
    return hashlib.sha256(json.dumps([record, context], sort_keys=True).encode()).hexdigest()


def reply_eligible(context):
    return (context.get("kind") == "request" and context.get("is_open") is True
            and context.get("request_status") == "submitted"
            and context.get("request_type") == "community-submission"
            and context.get("draft") is True)


def eligible(result, context):
    remaining = [f for f in result["findings"] if f["key"] != "SIZE" or not result["waiver"]]
    return (context.get("kind") == "request" and context.get("is_open") is True
            and context.get("request_status") == "submitted"
            and context.get("request_type") == "community-submission"
            and context.get("draft") is True and not remaining)


class H(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src 'self' data:; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        if getattr(self, "new_cookie", None):
            self.send_header("Set-Cookie", "mc_session=" + self.new_cookie
                             + "; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(raw)

    def _trusted(self):
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return False
        if ALLOWED_HOSTS:
            if host not in ALLOWED_HOSTS:
                return False
        elif not HOSTED:
            allowed = {f"127.0.0.1:{self.server.server_port}",
                       f"localhost:{self.server.server_port}",
                       f"[::1]:{self.server.server_port}"}
            if host not in allowed:
                return False
        origin = self.headers.get("Origin")
        if origin:
            parsed = urllib.parse.urlsplit(origin)
            if parsed.scheme not in ("http", "https") or parsed.netloc.lower() != host:
                return False
        return self.headers.get("Sec-Fetch-Site") not in ("cross-site",)

    def _archive_error(self, session, error):
        if isinstance(error, urllib.error.HTTPError):
            if error.code in (401, 403):
                if session:
                    session["verified"] = False
                message = ("The Archive denied access (HTTP " + str(error.code)
                           + "). Check that the moderator token is still valid.")
            elif error.code == 404:
                message = "The Archive could not find that request or record (HTTP 404)."
            elif 300 <= error.code < 400:
                message = "The Archive redirected the request. For credential safety, redirects are blocked."
            else:
                message = "The Archive returned HTTP " + str(error.code) + ". Try again later."
            return self._send(502, {"error": message})
        if isinstance(error, (urllib.error.URLError, TimeoutError, OSError)):
            return self._send(502, {"error": "Could not reach the Archive. Check your connection and retry."})
        message = str(error) if type(error) is ValueError else "The Archive returned incomplete or unsupported data."
        return self._send(400, {"error": message})

    def _session(self, create=False):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            sid = cookie["mc_session"].value if "mc_session" in cookie else None
        except Exception:
            sid = None
        with LOCK:
            now = time.monotonic()
            for key in list(SESSIONS):
                if now - SESSIONS[key]["touched"] > SESSION_TTL:
                    del SESSIONS[key]
            session = SESSIONS.get(sid)
            if session is None and create:
                sid = secrets.token_urlsafe(32)
                session = {"csrf": secrets.token_urlsafe(32), "token": STARTUP_TOKEN,
                           "review": None, "dashboard": None, "touched": now,
                           "send_in_progress": False,
                           "ai": dict(AI_DEFAULT) if AI_DEFAULT else None,
                           "ai_in_progress": False, "ai_revision": 0,
                           "verified": False, "revision": 0}
                SESSIONS[sid] = session
                self.new_cookie = sid
            if session:
                session["touched"] = now
            return session

    def do_GET(self):
        if not self._trusted():
            return self._send(403, {"error": "Only this local app may access the workspace."})
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/index.html": ("index.html", "text/html; charset=utf-8"),
                  "/favicon.svg": ("favicon.svg", "image/svg+xml"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/app.css": ("app.css", "text/css; charset=utf-8")}
        if path in assets:
            name, ctype = assets[path]
            with open(os.path.join(HERE, name), "rb") as file:
                return self._send(200, file.read(), ctype)
        if path == "/api/session":
            session = self._session(create=True)
            public_ai = ai_review.public_connection(session.get("ai"))
            return self._send(200, {"csrf": session["csrf"], "has_token": bool(session["token"]),
                                    "verified": session["verified"],
                                    "read_only": not COMMENTS_ENABLED,
                                    "archive_access": ("comments_only" if COMMENTS_ENABLED
                                                       else "read_only"),
                                    "actions_enabled": False,
                                    "comments_enabled": COMMENTS_ENABLED,
                                    "decisions_enabled": False,
                                    "ai_available": public_ai["available"],
                                    "ai": public_ai,
                                    "archive": ARCHIVE, "mock": ARCHIVE != PRODUCTION})
        if path == "/api/example":
            with open(os.path.join(HERE, "..", "fixtures", "base_clean.json")) as file:
                return self._send(200, json.load(file))
        if path == "/api/playbook":
            return self._send(200, PLAYBOOK)
        if path == "/api/dashboard" or path == "/api/dashboard/history" or path.startswith(
                "/api/dashboard/request/"):
            session = self._session()
            if not session:
                return self._send(403, {"error": "Your local session expired. Reload this page."})
            if not session["token"]:
                return self._send(401, {"error": "Add a moderator token to load the dashboard."})
            try:
                archive = Archive(ARCHIVE, session["token"])
                if path == "/api/dashboard":
                    refresh = urllib.parse.parse_qs(parsed.query).get("refresh") == ["1"]
                    cached = session.get("dashboard")
                    if not refresh and cached and time.monotonic() - cached["created"] < DASHBOARD_TTL:
                        return self._send(200, cached["value"])
                    revision = session["revision"]
                    value = dashboard_data.dashboard(archive)
                    with LOCK:
                        if revision != session["revision"]:
                            raise ValueError("The account changed while the dashboard was loading. Refresh it.")
                        session["dashboard"] = {"created": time.monotonic(), "value": value}
                        session["verified"] = True
                    return self._send(200, value)
                if path == "/api/dashboard/history":
                    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                    if not set(query).issubset({"status", "page", "size"}) or any(
                            len(values) != 1 for values in query.values()):
                        raise ValueError("Invalid history filters.")
                    status = query.get("status", ["accepted"])[0]
                    page = int(query.get("page", ["1"])[0])
                    size = int(query.get("size", ["25"])[0])
                    if page < 1 or size < 1 or size > 50:
                        raise ValueError("Invalid history page.")
                    return self._send(200, dashboard_data.history(archive, status, page, size))
                request_id = path.rsplit("/", 1)[-1]
                if not re.fullmatch(IDENTIFIER, request_id):
                    raise ValueError("Invalid request identifier.")
                revision = session["revision"]
                result = dashboard_data.detail(archive, request_id, verdict, PLAYBOOK)
                context = result["context"]
                timeline_fingerprint = result.pop("_timeline_fingerprint")
                review_id = secrets.token_urlsafe(24)
                allowed = eligible(result, context)
                with LOCK:
                    if revision != session["revision"]:
                        raise ValueError("The account changed while the request was loading. Refresh it.")
                    if session["send_in_progress"]:
                        raise ValueError("A reply is being sent. Wait for it to finish before opening another review.")
                    session["review"] = {
                        "id": review_id, "input": context["url"], "created": time.monotonic(),
                        "fingerprint": fingerprint(result["record"], context),
                        "timeline_fingerprint": timeline_fingerprint,
                        "eligible": allowed, "context": context,
                        "record": result["record"],
                        "verdict": {key: result.get(key) for key in
                                    ("status", "findings", "eyeball", "waiver")},
                        "ai": None,
                    }
                    session["verified"] = True
                result.update(review_id=review_id, handoff_eligible=allowed,
                              reply_eligible=COMMENTS_ENABLED and reply_eligible(context),
                              ai_eligible=(context.get("is_open") is True and
                                           result.get("queue_bucket") in ("new", "replied")),
                              verified=True)
                return self._send(200, result)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError,
                    ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
                return self._archive_error(session, error)
        return self._send(404, {"error": "Not found."})

    def _current_review(self, session, review_id):
        with LOCK:
            review = session.get("review")
            if not review or review_id != review["id"]:
                raise ValueError("This review is no longer current. Open the submission again.")
            if time.monotonic() - review["created"] > REVIEW_TTL:
                session["review"] = None
                raise ValueError("The review is older than 15 minutes. Open the submission again.")
            return review

    def _run_ai_advisory(self, session, body):
        with LOCK:
            connection = session.get("ai")
            if not ai_review.available(connection):
                return self._send(503, {"error": (
                    "Configure an AI provider in Settings: OpenRouter, DeepSeek, or any "
                    "OpenAI-compatible endpoint. The rule-based review still works."
                )})
            if session["send_in_progress"]:
                raise ValueError("A reply is being sent. Wait for it to finish before running AI review.")
            if session["ai_in_progress"]:
                return self._send(409, {"error": "An AI review is already running for this session."})
            review = self._current_review(session, body.get("review_id"))
            if review.get("ai") is not None:
                return self._send(200, dict(review["ai"], cached=True))
            if review["context"].get("is_open") is not True:
                raise ValueError("AI review runs only for open submissions.")
            review_id = review["id"]
            revision = session["revision"]
            ai_revision = session["ai_revision"]
            record = review["record"]
            assessment = review["verdict"]
            connection = dict(connection) if connection else None
            session["ai_in_progress"] = True
        try:
            result = ai_review.review(record, assessment, PLAYBOOK, connection=connection)
        except TimeoutError:
            return self._send(504, {"error": "AI review timed out. The rule-based findings are still available."})
        except ai_review.AIProviderError as error:
            return self._send(502, {"error": str(error)})
        except ValueError:
            return self._send(502, {"error": "AI review returned an invalid result. The rule-based findings are unchanged."})
        finally:
            with LOCK:
                session["ai_in_progress"] = False
        with LOCK:
            current = session.get("review")
            if (session["revision"] != revision or session["ai_revision"] != ai_revision or not current or
                    current["id"] != review_id or session["send_in_progress"]):
                return self._send(409, {"error": "The active review or AI provider changed while AI was working. Open it again."})
            current["ai"] = result
        return self._send(200, dict(result, cached=False))

    def _post_reply(self, session, body):
        if not COMMENTS_ENABLED:
            return self._send(405, {"error": "Comment sending is disabled for this workspace."})
        content = body.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Write a reply before sending it.")
        if len(content) > MAX_REPLY_CHARS:
            raise ValueError("The reply is too long to send safely.")
        content_html = dashboard_data.reply_html(content)
        with LOCK:
            if session["send_in_progress"]:
                raise ValueError("A reply is already being sent. Wait for it to finish.")
            review = self._current_review(session, body.get("review_id"))
            request_id = review["context"].get("request_id")
            confirmation = body.get("confirm")
            if (not isinstance(confirmation, str) or not isinstance(request_id, str) or
                    not secrets.compare_digest(confirmation, request_id)):
                raise ValueError("The confirmation does not match this request.")
            if body.get("acknowledge_external_send") is not True:
                raise ValueError("Confirm that this reply will be posted to the submitter.")
            if not reply_eligible(review["context"]):
                raise ValueError("Replies can be sent only to an open community submission draft.")
            if not review.get("timeline_fingerprint"):
                raise ValueError("Open this request from the Dashboard before sending a reply.")
            token = session["token"]
            if not token:
                raise ValueError("A moderator token is required to send a reply.")
            revision = session["revision"]
            review_id = review["id"]
            session["send_in_progress"] = True

        try:
            archive = Archive(ARCHIVE, token)
            record, context = archive.resolve(review["input"])
            timeline = archive.timeline(request_id)["hits"]
            record_changed = fingerprint(record, context) != review["fingerprint"]
            timeline_changed = (dashboard_data.timeline_fingerprint(timeline) !=
                                review["timeline_fingerprint"])
            with LOCK:
                current = session.get("review")
                local_changed = (session["revision"] != revision or session["token"] != token or
                                 not current or current["id"] != review_id)
                if record_changed or timeline_changed or local_changed or not reply_eligible(context):
                    session.update(review=None, dashboard=None,
                                   revision=session["revision"] + 1)
                    if timeline_changed:
                        change = "conversation"
                    elif record_changed:
                        change = "record"
                    elif not reply_eligible(context):
                        change = "request status"
                    else:
                        change = "active local review"
                    raise ValueError(
                        "The " + change + " changed since you opened it. "
                        "No reply was sent; reopen the request and review the latest version."
                    )
            try:
                created = CommentWriter(archive).post(request_id, content_html)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError,
                    ValueError, KeyError, TypeError):
                with LOCK:
                    session.update(review=None, dashboard=None,
                                   revision=session["revision"] + 1)
                return self._send(502, {"error": (
                    "The Archive did not confirm whether the reply was posted. "
                    "Refresh the conversation before trying again; the app will not retry automatically."
                )})
            with LOCK:
                session.update(review=None, dashboard=None, verified=True,
                               revision=session["revision"] + 1)
            return self._send(200, {"sent": True, "comment_id": created.get("id"),
                                    "message": "Reply posted. The queue will now be refreshed."})
        finally:
            with LOCK:
                session["send_in_progress"] = False

    def do_POST(self):
        if not self._trusted():
            return self._send(403, {"error": "Cross-origin requests are blocked."})
        session = self._session()
        if not session or not secrets.compare_digest(
                self.headers.get("X-CSRF-Token", ""), session["csrf"]):
            return self._send(403, {"error": "Your local session expired. Reload this page."})
        if self.path not in ("/api/account", "/api/disconnect", "/check", "/api/handoff",
                             "/api/ai-settings", "/api/ai-review", "/api/reply/send"):
            return self._send(405, {"error": (
                "This route is unavailable. The app supports reads, advisory AI review, "
                "and explicitly confirmed comments only; decisions and publishing stay in the Archive."
            )})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                return self._send(413, {"error": "Input must be between 1 byte and 2 MB."})
            if self.headers.get_content_type() != "application/json":
                return self._send(415, {"error": "Send application/json."})
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object.")
            if self.path == "/api/account":
                token = body.get("token")
                if not isinstance(token, str) or not token or len(token) > 4096 or any(
                        ord(c) < 33 or ord(c) > 126 for c in token):
                    raise ValueError("Enter a valid token without spaces or line breaks.")
                with LOCK:
                    if session["send_in_progress"]:
                        raise ValueError("A reply is being sent. Wait before changing accounts.")
                    session.update(token=token, review=None, dashboard=None, verified=False,
                                   revision=session["revision"] + 1)
                return self._send(200, {"has_token": True, "verified": False})
            if self.path == "/api/disconnect":
                with LOCK:
                    if session["send_in_progress"]:
                        raise ValueError("A reply is being sent. Wait before disconnecting.")
                    session.update(token="", review=None, dashboard=None, verified=False,
                                   revision=session["revision"] + 1)
                return self._send(200, {"has_token": False})
            if self.path == "/api/ai-settings":
                allowed = {"provider", "endpoint", "model", "api_key", "clear"}
                if not set(body).issubset(allowed):
                    raise ValueError("Unsupported AI settings field.")
                with LOCK:
                    if session["ai_in_progress"]:
                        raise ValueError("Wait for the current AI review before changing its provider.")
                    existing = session.get("ai") or {}
                    if body.get("clear") is True:
                        if set(body) != {"clear"}:
                            raise ValueError("Clear AI settings in a separate request.")
                        connection = None
                    else:
                        if "clear" in body:
                            raise ValueError("The AI settings request is invalid.")
                        api_key = body.get("api_key", existing.get("api_key", ""))
                        connection = ai_review.validate_connection(
                            body.get("provider", existing.get("provider", "custom")),
                            body.get("model", existing.get("model", "")),
                            api_key,
                            body.get("endpoint", existing.get("endpoint", "")),
                        )
                    session["ai"] = connection
                    session["ai_revision"] += 1
                    if session.get("review"):
                        session["review"]["ai"] = None
                public_ai = ai_review.public_connection(connection)
                return self._send(200, {"ai": public_ai,
                                        "ai_available": public_ai["available"]})
            if self.path == "/api/ai-review":
                return self._run_ai_advisory(session, body)
            if self.path == "/api/reply/send":
                return self._post_reply(session, body)
            token = session["token"]
            archive = Archive(ARCHIVE, token)
            if self.path == "/check":
                with LOCK:
                    if session["send_in_progress"]:
                        raise ValueError("A reply is being sent. Wait before starting another review.")
                    session["review"] = None
                    session["revision"] += 1
                    revision = session["revision"]
                text = body.get("input")
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("Paste an Archive link, a record ID, or record JSON.")
                record, context = archive.resolve(text)
                result = verdict(record)
                review_id = secrets.token_urlsafe(24)
                with LOCK:
                    if token != session["token"] or revision != session["revision"]:
                        raise ValueError("The account or active review changed during the check. Run it again.")
                    session["verified"] = bool(token) and context["kind"] != "json"
                    session["review"] = {
                        "id": review_id, "input": text, "created": time.monotonic(),
                        "fingerprint": fingerprint(record, context),
                        "eligible": eligible(result, context), "context": context,
                        "timeline_fingerprint": None, "record": record,
                        "verdict": {key: result.get(key) for key in
                                    ("status", "findings", "eyeball", "waiver")},
                        "ai": None,
                    }
                result.update(record=record, context=context, review_id=review_id,
                              handoff_eligible=eligible(result, context),
                              verified=session["verified"])
                return self._send(200, result)
            with LOCK:
                if session["send_in_progress"]:
                    raise ValueError("A reply is being sent. Wait before preparing another action.")
                review = self._current_review(session, body.get("review_id"))
                if not review["eligible"]:
                    raise ValueError("Resolve the findings and review an open submission before continuing.")
                checks = body.get("checks")
                required = ("scope", "description", "files", "findings")
                if not isinstance(checks, dict) or any(checks.get(key) is not True for key in required):
                    raise ValueError("Complete every human review check before continuing.")
                if body.get("confirm") != review["context"]["request_id"]:
                    raise ValueError("The confirmation does not match this request.")
                session["review"] = None
                session["revision"] += 1
                revision = session["revision"]
            record, context = archive.resolve(review["input"])
            if token != session["token"] or revision != session["revision"]:
                raise ValueError("The account or active review changed. Check the submission again.")
            if fingerprint(record, context) != review["fingerprint"] or not eligible(verdict(record), context):
                raise ValueError("The submission changed since your review. Check it again.")
            return self._send(200, {"url": context["url"], "published": False,
                                    "message": "Rechecked. Complete the final decision in the Archive."})
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                session["verified"] = False
                message = ("The Archive denied access (HTTP " + str(error.code)
                           + "). Load a moderator token with access to this submission, then retry.")
            elif error.code == 404:
                message = "The Archive could not find this request or draft (HTTP 404). Check the link and your access."
            elif 300 <= error.code < 400:
                message = "The Archive redirected the request. For credential safety, redirects are blocked."
            else:
                message = "The Archive returned HTTP " + str(error.code) + ". Try again later."
            return self._send(502, {"error": message})
        except (urllib.error.URLError, TimeoutError, OSError):
            return self._send(502, {"error": "Could not reach the Archive. Check your connection and retry."})
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            # Keep remote content and credential details out of browser errors.
            error = sys.exc_info()[1]
            message = str(error) if type(error) is ValueError else "The record or request has incomplete or unsupported metadata."
            return self._send(400, {"error": message})


def main():
    global STARTUP_TOKEN
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--open", action="store_true", help="Open the workspace in your browser.")
    args = parser.parse_args()
    if not HOSTED or ALLOW_ENV_TOKEN:
        STARTUP_TOKEN = local_token()
    Archive(ARCHIVE)
    try:
        server = ThreadingHTTPServer((HOST, PORT), H)
    except OSError as error:
        raise SystemExit(f"Could not start the workspace on {HOST}:{PORT}: {error}")
    display = "127.0.0.1" if HOST in ("0.0.0.0", "::") else HOST
    url = f"http://{display}:{server.server_port}"
    print(startup_message(url), flush=True)
    if HOSTED and not ALLOW_ENV_TOKEN:
        print("Hosted mode: no shared token loaded; each visitor connects their own.", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWorkspace closed. In-memory credentials cleared.")
    finally:
        server.server_close()
        SESSIONS.clear()


if __name__ == "__main__":
    main()
