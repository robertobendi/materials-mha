import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import deterministic


class ReferenceTests(unittest.TestCase):
    def record_with_reference(self, citation, resource_type="publication-article"):
        with (ROOT / "fixtures/base_clean.json").open() as source:
            record = json.load(source)
        record["custom_fields"]["mc_references"] = [{
            "ref_resource_type": resource_type,
            "ref_citation": citation,
        }]
        return record

    def findings(self, record):
        entries = [dict(value, key=key) for key, value in record["files"]["entries"].items()]
        before = copy.deepcopy(record)
        result = deterministic.check(record, entries)
        self.assertEqual(record, before)
        return result["findings"]

    def keys(self, citation, resource_type="publication-article"):
        return [finding["key"] for finding in self.findings(
            self.record_with_reference(citation, resource_type))]

    def test_preprint_without_link_uses_specific_reply(self):
        citation = "A. Author, B. Author, A computational materials study, arXiv preprint (2026)"
        self.assertEqual(self.keys(citation), ["ref7"])
        self.assertEqual(
            deterministic.canned("ref7"),
            "- Could you please provide a link (DOI or URL) to the preprint? Once your paper is "
            "published, you will be able to update the reference.",
        )

    def test_preprint_with_bare_doi_or_url_has_no_link_finding(self):
        cases = [
            "A. Author, A computational materials study, preprint, 10.48550/arXiv.2609.01234",
            "A. Author, A computational materials study, arXiv preprint, https://arxiv.org/abs/2609.01234",
        ]
        for citation in cases:
            with self.subTest(citation=citation):
                self.assertNotIn("ref7", self.keys(citation))

    def test_preprint_with_structured_archive_link_has_no_link_finding(self):
        record = self.record_with_reference(
            "A. Author, A study, arXiv preprint (2026)", "publication-preprint")
        record["custom_fields"]["mc_references"][0]["ref_link"] = \
            "https://arxiv.org/abs/2609.01234"
        self.assertNotIn("ref7", [finding["key"] for finding in self.findings(record)])

    def test_short_reference_with_structured_link_is_not_thin(self):
        record = self.record_with_reference("Supporting article")
        record["custom_fields"]["mc_references"][0]["ref_link"] = \
            "https://example.org/article"
        self.assertNotIn("ref2", [finding["key"] for finding in self.findings(record)])

    def test_reference_comment_contributes_to_completeness(self):
        record = self.record_with_reference("A. Author")
        record["custom_fields"]["mc_references"][0]["ref_comment"] = \
            "Submitted to Example Journal"
        self.assertNotIn("ref2", [finding["key"] for finding in self.findings(record)])

    def test_doi_url_needs_format_fix_but_not_preprint_link(self):
        citation = "A. Author, A computational materials study, preprint, https://doi.org/10.48550/arXiv.2609.01234"
        keys = self.keys(citation)
        self.assertIn("ref4", keys)
        self.assertNotIn("ref7", keys)

    def test_preprint_resource_type_is_also_detected(self):
        citation = "A. Author, B. Author, A computational materials study, submitted (2026)"
        self.assertEqual(self.keys(citation, "publication-preprint"), ["ref7"])

    def test_bare_arxiv_identifier_is_not_a_link(self):
        citation = "A. Author, B. Author, A computational materials study, arXiv:2609.01234"
        self.assertEqual(self.keys(citation), ["ref7"])

    def test_offline_checker_matches_preprint_rule(self):
        html = (ROOT / "app/checker.html").read_text()
        rules = html[html.index("const CANNED="):html.index("function human(")]
        cases = [
            self.record_with_reference(
                "A. Author, B. Author, A computational materials study, arXiv preprint (2026)"),
            self.record_with_reference(
                "A. Author, A computational materials study, preprint, 10.48550/arXiv.2609.01234"),
            self.record_with_reference(
                "A. Author, A computational materials study, preprint, https://doi.org/10.48550/arXiv.2609.01234"),
        ]
        structured = self.record_with_reference(
            "A. Author, A study, arXiv preprint (2026)", "publication-preprint")
        structured["custom_fields"]["mc_references"][0]["ref_link"] = \
            "https://arxiv.org/abs/2609.01234"
        cases.append(structured)
        linked = self.record_with_reference("Supporting article")
        linked["custom_fields"]["mc_references"][0]["ref_link"] = \
            "https://example.org/article"
        cases.append(linked)
        commented = self.record_with_reference("A. Author")
        commented["custom_fields"]["mc_references"][0]["ref_comment"] = \
            "Submitted to Example Journal"
        cases.append(commented)
        for record in cases:
            with self.subTest(citation=record["custom_fields"]["mc_references"][0]["ref_citation"]):
                expected = [finding["key"] for finding in self.findings(record)]
                code = rules + "\nprocess.stdout.write(JSON.stringify(verdict(" + json.dumps(record) + ")));"
                run = subprocess.run(["node"], input=code, text=True, capture_output=True, check=True)
                actual = json.loads(run.stdout)
                self.assertEqual([finding["key"] for finding in actual["findings"]], expected)


if __name__ == "__main__":
    unittest.main()
