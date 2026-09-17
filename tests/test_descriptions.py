import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import deterministic


class DescriptionTests(unittest.TestCase):
    def record(self, description):
        with (ROOT / "fixtures/base_clean.json").open() as source:
            record = json.load(source)
        record["metadata"]["description"] = description
        return record

    def findings(self, record):
        entries = [dict(value, key=key) for key, value in record["files"]["entries"].items()]
        return deterministic.check(record, entries)["findings"]

    def test_nonbreaking_space_after_period_is_not_missing_punctuation(self):
        record = self.record("<p>" + "This is a complete abstract sentence. " * 12 + "&nbsp;</p>")
        self.assertNotIn("d8", [finding["key"] for finding in self.findings(record)])

    def test_offline_checker_matches_entity_handling(self):
        record = self.record("<p>" + "This is a complete abstract sentence. " * 12 + "&#xA0;</p>")
        html = (ROOT / "app/checker.html").read_text()
        rules = html[html.index("const CANNED="):html.index("function human(")]
        code = rules + "\nprocess.stdout.write(JSON.stringify(verdict(" + json.dumps(record) + ")));"
        run = subprocess.run(["node"], input=code, text=True, capture_output=True, check=True)
        actual = json.loads(run.stdout)
        self.assertNotIn("d8", [finding["key"] for finding in actual["findings"]])


if __name__ == "__main__":
    unittest.main()
