import copy
import http.client
import json
import re
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import serve
from archive_client import Archive, validate_record

REQUEST_ID = "68e160cd-0e42-4c4a-8d21-5c1fadce821e"
LINK = "https://archive.materialscloud.org/communities/mcarchive/requests/" + REQUEST_ID
with (ROOT / "fixtures/base_clean.json").open() as source:
    CLEAN = json.load(source)


class FakeArchive(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        server = self.server
        server.calls.append((self.command, self.path, self.headers.get("Authorization")))
        status, result = 200, {}
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if server.redirect:
            self.send_response(302)
            self.send_header("Location", server.redirect)
            self.end_headers()
            return
        if server.denied or self.headers.get("Authorization") != "Bearer test-token":
            status = 403
        elif path == "/api/me":
            result = {"id": "moderator", "email": "roberto.bendinelli@example.org", "roles": []}
        elif path in ("/api/requests", "/api/requests/"):
            term = query.get("q", [""])[0]
            hits = [server.request]
            if "status:accepted" in term or "status:declined" in term:
                hits = []
            result = {"hits": {"hits": hits, "total": len(hits)}}
        elif path.endswith("/timeline"):
            result = {"hits": {"hits": server.timeline, "total": len(server.timeline)}}
        elif path.startswith("/api/requests/"):
            result = server.request
        elif self.path.startswith("/api/records?q="):
            result = {"hits": {"hits": [{"id": "abc12-def34"}]}}
        elif self.path.endswith("/files"):
            result = {"entries": server.record["files"]["entries"]}
        elif self.path in ("/api/records/abc12-def34/draft", "/api/records/abc12-def34"):
            result = copy.deepcopy(server.record)
            result["files"].pop("entries")
        else:
            status = 404
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def do_POST(self):
        self.server.calls.append((self.command, self.path, self.headers.get("Authorization")))
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        status, result = 404, {"message": "not found"}
        if (self.path == "/api/requests/" + REQUEST_ID + "/comments" and
                self.headers.get("Authorization") == "Bearer test-token"):
            body = json.loads(raw)
            self.server.posted_comments.append(body)
            comment = {
                "id": "comment-" + str(len(self.server.posted_comments)), "type": "C",
                "created": "2026-09-08T12:00:00", "updated": "2026-09-08T12:00:00",
                "created_by": {"user": "moderator"}, "payload": body["payload"],
            }
            self.server.timeline.append(comment)
            status, result = 201, comment
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())


class WorkspaceTests(unittest.TestCase):
    def test_startup_banner_is_safe_for_default_windows_console(self):
        banner = serve.startup_message("http://127.0.0.1:8000")
        self.assertIn("http://127.0.0.1:8000", banner)
        banner.encode("cp1252")

    def test_favicon_is_served_as_a_static_asset(self):
        status, content, headers = self.call("GET", "/favicon.svg")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("image/svg+xml"))
        self.assertIn(b"<svg", content)

    def test_javascript_id_selectors_exist_in_dashboard_markup(self):
        script = (ROOT / "app" / "app.js").read_text(encoding="utf-8")
        markup = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
        selectors = set(re.findall(r"\$\('#([A-Za-z][A-Za-z0-9_-]*)'\)", script))
        element_ids = set(re.findall(r'\bid="([A-Za-z][A-Za-z0-9_-]*)"', markup))
        self.assertEqual(selectors - element_ids, set())

    @classmethod
    def setUpClass(cls):
        cls.archive = ThreadingHTTPServer(("127.0.0.1", 0), FakeArchive)
        cls.app = ThreadingHTTPServer(("127.0.0.1", 0), serve.H)
        cls.threads = []
        for server in (cls.archive, cls.app):
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
            thread.start()
            cls.threads.append(thread)
        cls.base = f"http://127.0.0.1:{cls.archive.server_port}"
        cls.config = patch.object(serve, "ARCHIVE", cls.base)
        cls.config.start()

    @classmethod
    def tearDownClass(cls):
        cls.config.stop()
        for server in (cls.app, cls.archive):
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join()

    def setUp(self):
        serve.SESSIONS.clear()
        self.archive.calls = []
        self.archive.posted_comments = []
        self.archive.denied = False
        self.archive.redirect = None
        self.archive.record = copy.deepcopy(CLEAN)
        self.archive.record["id"] = "abc12-def34"
        self.archive.request = {"id": REQUEST_ID, "topic": {"record": "abc12-def34"},
                                "type": "community-submission", "is_open": True,
                                "status": "submitted", "updated": "2026-09-08T10:00:00",
                                "created": "2026-09-08T09:00:00", "title": "Clean record",
                                "created_by": {"user": "submitter"},
                                "receiver": {"community": "30074283-2e48-4526-b2df-c3c2191db3cb"}}
        self.archive.timeline = []
        self.cookie = ""
        self.csrf = ""
        status, result, headers = self.call("GET", "/api/session")
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";")[0]
        self.csrf = result["csrf"]
        self.assertFalse(result["read_only"])
        self.assertTrue(result["comments_enabled"])
        self.assertFalse(result["decisions_enabled"])
        self.assertIn("ai", result)
        self.assertNotIn("api_key", result["ai"])

    def call(self, method, path, body=None, extra=None):
        headers = {"Cookie": self.cookie, "X-CSRF-Token": self.csrf, "Content-Type": "application/json"}
        headers.update(extra or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.app.server_port, timeout=5)
        connection.request(method, path, None if body is None else json.dumps(body), headers)
        response = connection.getresponse()
        raw = response.read()
        content = json.loads(raw) if response.getheader("Content-Type", "").startswith("application/json") else raw
        result = response.status, content, dict(response.getheaders())
        connection.close()
        return result

    def load(self, text=LINK):
        self.assertEqual(self.call("POST", "/api/account", {"token": "test-token"})[0], 200)
        status, result, _ = self.call("POST", "/check", {"input": text})
        self.assertEqual(status, 200, result)
        return result

    def handoff(self, result, **overrides):
        body = {"review_id": result["review_id"], "confirm": REQUEST_ID,
                "checks": {key: True for key in ("scope", "description", "files", "findings")}}
        body.update(overrides)
        return self.call("POST", "/api/handoff", body)

    def open_detail(self):
        self.assertEqual(self.call("POST", "/api/account", {"token": "test-token"})[0], 200)
        status, result, _ = self.call("GET", "/api/dashboard/request/" + REQUEST_ID)
        self.assertEqual(status, 200, result)
        return result

    def send_reply(self, result, **overrides):
        body = {"review_id": result["review_id"], "confirm": REQUEST_ID,
                "acknowledge_external_send": True, "content": "Thank you.\n\nPlease update the README."}
        body.update(overrides)
        return self.call("POST", "/api/reply/send", body)

    def test_request_resolution_fetches_complete_files_with_get_only(self):
        result = self.load()
        self.assertEqual(result["context"]["record_id"], "abc12-def34")
        self.assertTrue(result["verified"])
        self.assertTrue(result["handoff_eligible"])
        self.assertEqual(len(result["record"]["files"]["entries"]), 3)
        self.assertEqual([call[1] for call in self.archive.calls], [
            "/api/requests/" + REQUEST_ID, "/api/records/abc12-def34/draft",
            "/api/records/abc12-def34/draft/files"])
        self.assertTrue(all(method == "GET" and auth == "Bearer test-token"
                            for method, _, auth in self.archive.calls))

    def test_dashboard_and_detail_are_get_only(self):
        self.assertEqual(self.call("POST", "/api/account", {"token": "test-token"})[0], 200)
        status, board, _ = self.call("GET", "/api/dashboard?refresh=1")
        self.assertEqual(status, 200, board)
        self.assertEqual(board["summary"]["open"], 1)
        self.assertEqual(board["summary"]["new"], 1)
        self.assertEqual(board["account"]["display_name"], "Roberto Bendinelli")
        status, detail, _ = self.call("GET", "/api/dashboard/request/" + REQUEST_ID)
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["context"]["record_id"], "abc12-def34")
        self.assertEqual(detail["request"]["record_url"], self.base + "/uploads/abc12-def34")
        self.assertTrue(detail["reply_draft"] == "" or "Materials Cloud Archive" in detail["reply_draft"])
        self.assertTrue(all(method == "GET" for method, _, _ in self.archive.calls))
        before = len(self.archive.calls)
        self.assertEqual(self.call("POST", "/api/actions/comment", {"request_id": REQUEST_ID})[0], 405)
        self.assertEqual(len(self.archive.calls), before)

    def test_reply_requires_preview_confirmation_and_posts_one_escaped_comment(self):
        result = self.open_detail()
        before = len(self.archive.calls)
        self.assertEqual(self.send_reply(result, confirm="wrong")[0], 400)
        self.assertEqual(self.send_reply(result, acknowledge_external_send=False)[0], 400)
        self.assertEqual(len(self.archive.calls), before)

        status, sent, _ = self.send_reply(
            result, content="<script>alert('no')</script>\n\nThank you & best regards")
        self.assertEqual(status, 200, sent)
        self.assertTrue(sent["sent"])
        self.assertEqual(len(self.archive.posted_comments), 1)
        payload = self.archive.posted_comments[0]
        self.assertEqual(payload["payload"]["format"], "html")
        self.assertIn("&lt;script&gt;", payload["payload"]["content"])
        self.assertNotIn("<script>", payload["payload"]["content"])
        self.assertIn("&amp;", payload["payload"]["content"])
        post_calls = [call for call in self.archive.calls if call[0] == "POST"]
        self.assertEqual(post_calls, [("POST", "/api/requests/" + REQUEST_ID + "/comments",
                                      "Bearer test-token")])
        self.assertEqual(self.send_reply(result)[0], 400)
        self.assertEqual(len(self.archive.posted_comments), 1)

    def test_new_timeline_event_invalidates_reply_without_posting(self):
        result = self.open_detail()
        self.archive.timeline.append({
            "id": "new-author-comment", "type": "C", "created_by": {"user": "submitter"},
            "created": "2026-09-08T11:55:00", "updated": "2026-09-08T11:55:00",
            "payload": {"content": "I changed something", "format": "html"},
        })
        status, response, _ = self.send_reply(result)
        self.assertEqual(status, 400, response)
        self.assertIn("changed", response["error"])
        self.assertEqual(self.archive.posted_comments, [])
        self.assertIsNone(next(iter(serve.SESSIONS.values()))["review"])

    def test_ai_advisory_is_cached_and_never_writes_to_archive(self):
        status, saved, _ = self.call("POST", "/api/ai-settings", {
            "endpoint": "http://127.0.0.1:8765/v1",
            "model": "test-model",
            "api_key": "test-ai-key",
        })
        self.assertEqual(status, 200, saved)
        result = self.open_detail()
        advice = {
            "scope": {"verdict": "computational", "confidence": "high", "reason": "Simulation."},
            "description": {"ok": True, "reason": "Abstract-like."},
            "suggested_action": "human_review", "model_suggested_action": "human_review",
            "reply_lines": [], "moderator_note": "Inspect files.", "provider": "test",
            "advisory_only": True, "data_shared": "minimal metadata",
        }
        with patch.object(serve.ai_review, "review", return_value=advice) as reviewer:
            status, first, _ = self.call("POST", "/api/ai-review", {"review_id": result["review_id"]})
            self.assertEqual(status, 200, first)
            self.assertFalse(first["cached"])
            status, second, _ = self.call("POST", "/api/ai-review", {"review_id": result["review_id"]})
            self.assertEqual(status, 200, second)
            self.assertTrue(second["cached"])
            reviewer.assert_called_once()
            sent_record, sent_verdict, _ = reviewer.call_args.args
            self.assertNotIn("conversation", sent_record)
            self.assertNotIn("conversation", sent_verdict)
            connection = reviewer.call_args.kwargs["connection"]
            self.assertEqual(connection, {
                "provider": "custom",
                "endpoint": "http://127.0.0.1:8765/v1",
                "model": "test-model",
                "api_key": "test-ai-key",
            })
            self.assertNotIn("test-ai-key", json.dumps(first))
            self.assertNotIn("test-ai-key", json.dumps(second))
        self.assertFalse(any(call[0] == "POST" for call in self.archive.calls))

    def test_ai_settings_are_memory_only_preserve_key_and_clear_cached_review(self):
        result = self.open_detail()
        session = next(iter(serve.SESSIONS.values()))
        session["review"]["ai"] = {"provider": "old cached result"}
        status, saved, _ = self.call("POST", "/api/ai-settings", {
            "endpoint": "http://127.0.0.1:8765/v1/chat/completions",
            "model": "test-model",
            "api_key": "test-ai-key",
        })
        self.assertEqual(status, 200, saved)
        self.assertTrue(saved["ai_available"])
        self.assertEqual(saved["ai"]["endpoint"], "http://127.0.0.1:8765/v1")
        self.assertTrue(saved["ai"]["has_key"])
        self.assertNotIn("api_key", saved["ai"])
        self.assertNotIn("test-ai-key", json.dumps(saved))
        self.assertEqual(session["ai"]["api_key"], "test-ai-key")
        self.assertIsNone(session["review"]["ai"])

        status, updated, _ = self.call("POST", "/api/ai-settings", {
            "endpoint": "http://localhost:8765/v1",
            "model": "replacement-model",
        })
        self.assertEqual(status, 200, updated)
        self.assertEqual(session["ai"]["api_key"], "test-ai-key")
        public_session = self.call("GET", "/api/session")[1]
        self.assertNotIn("test-ai-key", json.dumps(public_session))
        self.assertNotIn("api_key", public_session["ai"])

        status, cleared, _ = self.call("POST", "/api/ai-settings", {"clear": True})
        self.assertEqual(status, 200, cleared)
        self.assertFalse(cleared["ai"]["configured"])
        self.assertIsNone(session["ai"])

    def test_ai_settings_reject_insecure_or_credential_bearing_endpoints(self):
        for endpoint in (
                "http://api.example.test/v1",
                "https://user:password@api.example.test/v1",
                "https://api.example.test/v1?api_key=secret"):
            with self.subTest(endpoint=endpoint):
                status, response, _ = self.call("POST", "/api/ai-settings", {
                    "endpoint": endpoint, "model": "test-model", "api_key": "test-ai-key",
                })
                self.assertEqual(status, 400, response)
        self.assertIsNone(next(iter(serve.SESSIONS.values()))["ai"])

    def test_handoff_requires_checks_confirmation_and_rechecks(self):
        result = self.load()
        self.assertEqual(self.handoff(result, checks={})[0], 400)
        self.assertEqual(self.handoff(result, confirm="wrong-target")[0], 400)
        self.assertEqual(self.handoff(result, checks={key: "true" for key in
                         ("scope", "description", "files", "findings")})[0], 400)
        count = len(self.archive.calls)
        status, handoff, _ = self.handoff(result)
        self.assertEqual(status, 200, handoff)
        self.assertFalse(handoff["published"])
        self.assertEqual(handoff["url"], self.base + "/communities/mcarchive/requests/" + REQUEST_ID)
        self.assertEqual(len(self.archive.calls), count + 3)
        self.assertEqual(self.handoff(result)[0], 400)
        self.assertTrue(all(call[0] == "GET" for call in self.archive.calls))

    def test_changes_invalidate_handoff(self):
        for change in ("metadata", "files", "status"):
            with self.subTest(change=change):
                self.setUp()
                result = self.load()
                if change == "metadata":
                    self.archive.record["metadata"]["title"] += " changed"
                elif change == "files":
                    self.archive.record["files"]["entries"]["README.md"]["size"] += 1
                else:
                    self.archive.request.update(is_open=False, status="accepted")
                self.assertEqual(self.handoff(result)[0], 400)

    def test_expired_or_replaced_review_is_rejected(self):
        result = self.load()
        session = next(iter(serve.SESSIONS.values()))
        session["review"]["created"] = time.monotonic() - serve.REVIEW_TTL - 1
        self.assertEqual(self.handoff(result)[0], 400)
        result = self.load()
        self.load()
        self.assertEqual(self.handoff(result)[0], 400)

    def test_json_and_closed_requests_cannot_prepare_publication(self):
        status, result, _ = self.call("POST", "/check", {"input": json.dumps(CLEAN)})
        self.assertEqual(status, 200)
        self.assertFalse(result["handoff_eligible"])
        self.assertEqual(self.handoff(result)[0], 400)
        self.assertEqual(self.archive.calls, [])
        self.archive.request.update(is_open=False, status="accepted")
        self.assertFalse(self.load()["handoff_eligible"])

    def test_funding_exception_does_not_hide_other_findings(self):
        self.archive.record["files"]["entries"]["trajectories.h5"]["size"] = 60_000_000_000
        result = self.load()
        self.assertTrue(result["waiver"])
        self.assertTrue(result["handoff_eligible"])
        self.archive.record["metadata"]["subjects"] = []
        self.assertFalse(self.load()["handoff_eligible"])
        self.archive.record["metadata"]["funding"] = []
        self.assertFalse(self.load()["handoff_eligible"])

    def test_funder_acronyms_must_be_whole_words(self):
        for name, expected in (("Commercial research foundation", False),
                               ("MARVELous research", False), ("NCCR MARVEL", True),
                               ("ERC (European Research Council)", True), ("SNSF", True)):
            with self.subTest(funder=name):
                record = copy.deepcopy(CLEAN)
                record["metadata"]["funding"] = [{"funder": {"name": name}}]
                self.assertEqual(serve.verdict(record)["waiver"], expected)

    def test_account_is_memory_only_and_disconnect_invalidates_review(self):
        result = self.load()
        self.assertNotIn("test-token", json.dumps(result))
        self.assertNotIn("test-token", json.dumps(self.call("GET", "/api/session")[1]))
        self.assertEqual(self.call("POST", "/api/disconnect", {})[0], 200)
        self.assertEqual(self.handoff(result)[0], 400)
        self.assertEqual(next(iter(serve.SESSIONS.values()))["token"], "")
        self.assertEqual(self.call("POST", "/check", {"input": LINK})[0], 502)

    def test_host_origin_csrf_and_write_routes_are_blocked(self):
        for extra in ({"Host": "attacker.example"}, {"Origin": "https://attacker.example"},
                      {"Sec-Fetch-Site": "cross-site"}, {"X-CSRF-Token": "wrong"}):
            self.assertEqual(self.call("POST", "/check", {"input": LINK}, extra)[0], 403)
        for route in ("/api/publish", "/api/requests/x/actions/accept", "/api/requests/x/comments", "/ai"):
            self.assertEqual(self.call("POST", route, {})[0], 405)
        self.assertEqual(self.archive.calls, [])
        headers = self.call("GET", "/")[2]
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_hosted_mode_trusts_only_the_configured_host_and_origin(self):
        with patch.object(serve, "HOSTED", True), \
                patch.object(serve, "ALLOWED_HOSTS", {"mod.example.org"}):
            self.assertEqual(self.call("GET", "/api/session", None,
                                       {"Host": "mod.example.org"})[0], 200)
            self.assertEqual(self.call("GET", "/api/session", None,
                                       {"Host": "mod.example.org",
                                        "Origin": "https://mod.example.org"})[0], 200)
            self.assertEqual(self.call("GET", "/api/session", None,
                                       {"Host": "attacker.example"})[0], 403)
            self.assertEqual(self.call("GET", "/api/session", None,
                                       {"Host": "mod.example.org",
                                        "Origin": "https://attacker.example"})[0], 403)

    def test_bad_links_and_redirects_never_leak_credentials(self):
        client = Archive(self.base, "test-token")
        for text in ("https://evil.example/records/abc", "https://archive.materialscloud.org.evil/records/abc",
                     "https://user:password@archive.materialscloud.org/records/abc", "../../secrets",
                     "https://archive.materialscloud.org/api/requests/x/actions/accept"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                client.resolve(text)
        for path in ("/api/requests/x/actions/accept", "//evil.example", "/api/records/../../secret"):
            with self.assertRaises(ValueError):
                client.get(path)
        self.assertEqual(self.archive.calls, [])
        self.archive.redirect = f"http://127.0.0.1:{self.app.server_port}/capture"
        with self.assertRaises(urllib.error.HTTPError):
            client.resolve(LINK)
        self.assertEqual(len(self.archive.calls), 1)

    def test_denied_draft_is_not_replaced_by_published_record(self):
        self.archive.denied = True
        with self.assertRaises(urllib.error.HTTPError):
            Archive(self.base, "test-token").resolve("abc12-def34")
        self.assertEqual(len(self.archive.calls), 1)

    def test_wrong_request_or_record_identity_fails_closed(self):
        self.archive.request["id"] = "other-request"
        self.call("POST", "/api/account", {"token": "test-token"})
        self.assertEqual(self.call("POST", "/check", {"input": LINK})[0], 400)
        self.archive.request["id"] = REQUEST_ID
        self.archive.record["id"] = "other-record"
        self.assertEqual(self.call("POST", "/check", {"input": LINK})[0], 400)

    def test_conflicting_rors_are_review_questions_not_submitter_feedback(self):
        record = copy.deepcopy(CLEAN)
        record["metadata"]["creators"][0]["affiliations"] = [{"name": "University", "id": "01kq0pv72"}]
        record["metadata"]["creators"][1]["affiliations"] = [{"name": "University", "id": "04h9pn542"}]
        status, result, _ = self.call("POST", "/check", {"input": json.dumps(record)})
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "warn")
        self.assertEqual(result["message"], "")
        self.assertEqual(len(result["eyeball"]), 1)
        self.assertFalse(result["handoff_eligible"])

    def test_account_change_during_handoff_invalidates_it(self):
        result = self.load()
        original = Archive.resolve

        def change_account(archive, text):
            resolved = original(archive, text)
            session = next(iter(serve.SESSIONS.values()))
            session["revision"] += 1
            return resolved

        with patch.object(Archive, "resolve", change_account):
            self.assertEqual(self.handoff(result)[0], 400)

    def test_legacy_record_and_mcid_links(self):
        for text in ("2026.132", "https://archive.materialscloud.org/record/2026.132",
                     "https://archive.materialscloud.org/records/abc12-def34"):
            result = self.load(text)
            self.assertEqual(result["context"]["record_id"], "abc12-def34")
            self.assertFalse(result["handoff_eligible"])

    def test_incomplete_files_fail_closed(self):
        for value in (None, -1, "100", True):
            record = copy.deepcopy(CLEAN)
            record["files"]["entries"]["README.md"]["size"] = value
            with self.subTest(size=value), self.assertRaises(ValueError):
                validate_record(record)
        record = copy.deepcopy(CLEAN)
        record["files"]["count"] = 100
        with self.assertRaises(ValueError):
            validate_record(record)
        record = copy.deepcopy(CLEAN)
        record["files"]["entries"]["README.md"]["status"] = "pending"
        with self.assertRaises(ValueError):
            validate_record(record)

    def test_malformed_input_is_recoverable(self):
        for value in (None, 123, [], "{", '{"metadata": {}}', '{"metadata": null}'):
            self.assertEqual(self.call("POST", "/check", {"input": value})[0], 400)
        self.assertEqual(self.call("POST", "/api/account", {"token": "secret\nheader"})[0], 400)
        self.assertEqual(self.call("POST", "/check", {"input": json.dumps(CLEAN)})[0], 200)

    def test_sessions_are_isolated_and_expire(self):
        self.load()
        original_cookie = self.cookie
        self.cookie = ""
        status, result, headers = self.call("GET", "/api/session")
        self.assertFalse(result["has_token"])
        self.assertNotEqual(headers["Set-Cookie"].split(";")[0], original_cookie)
        self.cookie = original_cookie
        session = serve.SESSIONS[original_cookie.split("=")[1]]
        session["touched"] = time.monotonic() - serve.SESSION_TTL - 1
        self.assertEqual(self.call("POST", "/check", {"input": LINK})[0], 403)

    def test_existing_demo_fixtures_remain_reviewable(self):
        for path in (ROOT / "fixtures/demo").glob("*.json"):
            with self.subTest(fixture=path.name), path.open() as source:
                record = json.load(source)
                result = serve.verdict(validate_record(record))
                self.assertIn(result["status"], ("ok", "warn", "changes", "stop"))


if __name__ == "__main__":
    unittest.main()
