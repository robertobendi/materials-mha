import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import deterministic


class ResourceTypeTests(unittest.TestCase):
    def record(self):
        with (ROOT / "fixtures/base_clean.json").open() as source:
            record = json.load(source)
        record["metadata"]["resource_type"] = {"id": "software"}
        return record

    def check(self, record):
        entries = [dict(value, key=key) for key, value in record["files"]["entries"].items()]
        return deterministic.check(record, entries)

    def test_archive_requires_human_inspection_not_submitter_feedback(self):
        result = self.check(self.record())
        self.assertNotIn("rt1", [finding["key"] for finding in result["findings"]])
        self.assertIn("REVIEW", [finding["key"] for finding in result["findings"]])

    def test_no_code_or_archive_keeps_resource_type_finding(self):
        record = self.record()
        record["files"]["entries"].pop("training_set.tar.gz")
        record["files"]["count"] -= 1
        self.assertIn("rt1", [finding["key"] for finding in self.check(record)["findings"]])

    def test_offline_checker_uses_same_archive_guard(self):
        record = self.record()
        html = (ROOT / "app/checker.html").read_text()
        rules = html[html.index("const CANNED="):html.index("function human(")]
        code = rules + "\nprocess.stdout.write(JSON.stringify(verdict(" + json.dumps(record) + ")));"
        run = subprocess.run(["node"], input=code, text=True, capture_output=True, check=True)
        actual = json.loads(run.stdout)
        self.assertNotIn("rt1", [finding["key"] for finding in actual["findings"]])
        self.assertTrue(any("inside an archive" in item for item in actual["eyeball"]))


if __name__ == "__main__":
    unittest.main()
