import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import dashboard


with (ROOT / "rules" / "precedents.json").open(encoding="utf-8") as source:
    PLAYBOOK = json.load(source)


class DashboardTests(unittest.TestCase):
    def request(self):
        return {
            "id": "request-1", "title": "Example", "status": "submitted", "is_open": True,
            "created": "2026-09-01T10:00:00Z", "updated": "2026-09-01T10:00:00Z",
            "created_by": {"user": "author"}, "topic": {"record": "record-1"},
        }

    def comment(self, user, content="Hello"):
        return {"id": user + "-comment", "type": "C", "created_by": {"user": user},
                "created": "2026-09-01T11:00:00Z", "updated": "2026-09-01T11:00:00Z",
                "payload": {"content": content, "format": "html"}}

    def test_queue_buckets_new_replied_and_waiting(self):
        self.assertEqual(dashboard.queue_row(self.request(), [], "moderator")["bucket"], "new")
        timeline = [self.comment("moderator"), self.comment("author")]
        self.assertEqual(dashboard.queue_row(self.request(), timeline, "moderator")["bucket"], "replied")
        timeline.append(self.comment("1965"))
        self.assertEqual(dashboard.queue_row(self.request(), timeline, "moderator")["bucket"], "waiting")

    def test_private_html_is_reduced_to_plain_text(self):
        value = dashboard.plain("<p>Hello &amp; welcome</p><script>bad()</script><div>Next</div>")
        self.assertNotIn("<", value)
        self.assertIn("Hello & welcome", value)
        self.assertIn("Next", value)

    def test_reply_html_escapes_markup_and_preserves_paragraphs(self):
        value = dashboard.reply_html("Hello <author> & team\nline two\n\nBest regards")
        self.assertEqual(
            value,
            "<p>Hello &lt;author&gt; &amp; team<br>line two</p><p>Best regards</p>",
        )
        with self.assertRaises(ValueError):
            dashboard.reply_html("\x00unsafe")

    def test_reply_uses_historical_wrapper_and_field_order(self):
        findings = [
            {"key": "f1", "text": "- Add a README."},
            {"key": "t1", "text": "- Use sentence case."},
            {"key": "SIZE", "text": "Too large."},
        ]
        reply = dashboard.compose_reply(findings, PLAYBOOK)
        self.assertTrue(reply.startswith("Thank you for your submission"))
        self.assertLess(reply.index("Title"), reply.index("Files"))
        self.assertNotIn("Too large", reply)
        self.assertTrue(reply.endswith("Best regards"))

    def test_follow_up_acknowledges_changes(self):
        reply = dashboard.compose_reply(
            [{"key": "ref7", "text": "- Please add a link."}], PLAYBOOK, follow_up=True)
        self.assertTrue(reply.startswith("Thank you for your message and for the changes you made."))

    def test_moderator_side_doi_edit_is_not_put_in_author_reply(self):
        reply = dashboard.compose_reply([
            {"key": "ref4", "text": "- Please rewrite the DOI."},
            {"key": "f1", "text": "- Please add a README."},
        ], PLAYBOOK)
        self.assertNotIn("rewrite the DOI", reply)
        self.assertIn("add a README", reply)


if __name__ == "__main__":
    unittest.main()
