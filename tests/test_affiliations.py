import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import deterministic


class AffiliationTests(unittest.TestCase):
    def result(self, affiliations):
        with (ROOT / "fixtures/base_clean.json").open() as source:
            record = json.load(source)
        record["metadata"]["title"] = "Navigating Sparse Singlet Fission Chemical Space: An Intelligent Generative-Predictive Paradigm"
        record["metadata"]["creators"] = [
            {"person_or_org": {"name": f"Author {index}"}, "affiliations": value}
            for index, value in enumerate(affiliations)
        ]
        before = copy.deepcopy(record)
        result = deterministic.check(record, [dict(value, key=key) for key, value in record["files"]["entries"].items()])
        self.assertEqual(record, before)
        return record, result

    def test_shared_ror_removes_false_address_requests_but_keeps_title(self):
        linked = {"name": "South China Normal University", "id": "01kq0pv72"}
        plain = {"name": "South China Normal University"}
        _, result = self.result([[linked], [linked], [plain], [plain], [linked]])
        self.assertEqual([f["key"] for f in result["findings"]], ["t1"])
        self.assertEqual(result["affiliation_rors"], [["01kq0pv72"]] * 5)

    def test_missing_ror_still_requires_address_for_that_institution(self):
        _, result = self.result([[{"name": "University A", "id": "01kq0pv72"}],
                                 [{"name": "University B"}]])
        self.assertEqual([f["key"] for f in result["findings"]], ["t1", "af2"])
        self.assertIn("University B", result["findings"][1]["detail"])

    def test_conflicting_rors_do_not_resolve_unlinked_affiliation(self):
        _, result = self.result([[{"name": "University", "id": "01kq0pv72"}],
                                 [{"name": "University", "id": "04h9pn542"}],
                                 [{"name": "University"}]])
        self.assertIsNone(result["affiliation_rors"][2][0])
        self.assertNotIn("af2", [f["key"] for f in result["findings"]])
        reviews = [f for f in result["findings"] if f["key"] == "REVIEW"]
        self.assertEqual(len(reviews), 1)
        self.assertIn("conflicting ROR", reviews[0]["text"])

    def test_repeated_unresolved_institution_has_one_warning_with_all_authors(self):
        _, result = self.result([[{"name": "University"}], [{"name": " university "}]])
        warnings = [f for f in result["findings"] if f["key"] == "af2"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("Author 0, Author 1", warnings[0]["detail"])

    def test_empty_or_non_string_id_is_not_ror_evidence(self):
        for identifier in (" ", None, False, True, 123):
            with self.subTest(identifier=identifier):
                _, result = self.result([[{"name": "University", "id": identifier}]])
                self.assertEqual(result["affiliation_rors"], [[None]])
                self.assertIn("af2", [f["key"] for f in result["findings"]])

    def test_new_submission_title_does_not_trigger_capitalization_warning(self):
        record, _ = self.result([[{"name": "Flatiron Institute", "id": "00sekdz59"}],
                                 [{"name": "Seoul National University", "id": "04h9pn542"}],
                                 [{"name": "Sunchon National University", "id": "043jqrs76"}],
                                 [{"name": "Sunchon National University"}]])
        record["metadata"]["title"] = ("Accurate calculation of Wannier centers, position matrix, and composite "
                                      "operators using translationally equivariant and higher-order finite differences")
        entries = [dict(value, key=key) for key, value in record["files"]["entries"].items()]
        self.assertEqual(deterministic.check(record, entries)["findings"], [])

    def test_only_case_and_whitespace_are_normalized(self):
        _, result = self.result([[{"name": "South China Normal University", "id": "01kq0pv72"}],
                                 [{"name": " south  china normal university "}],
                                 [{"name": "South China Normal University, Another Campus"}]])
        self.assertEqual(result["affiliation_rors"], [["01kq0pv72"], ["01kq0pv72"], [None]])
        self.assertEqual(len([f for f in result["findings"] if f["key"] == "af2"]), 1)

    def test_missing_affiliation_and_complete_unlinked_address(self):
        _, result = self.result([[], [{"name": "Physics Department, University, City, Country"}]])
        self.assertEqual([f["key"] for f in result["findings"]], ["t1", "af3"])

    def test_offline_checker_matches_shared_ror_resolution(self):
        html = (ROOT / "app/checker.html").read_text()
        helper = html[html.index("function affiliationName("):html.index("function rules(")]
        creators = [{"affiliations": [{"name": "University", "id": "01kq0pv72"}]},
                    {"affiliations": [{"name": " university "}]},
                    {"affiliations": [{"name": "University, Other Campus"}]}]
        code = helper + "\nprocess.stdout.write(JSON.stringify(affiliationRors(" + json.dumps(creators) + ")));"
        run = subprocess.run(["node", "-e", code], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(run.stdout), deterministic.affiliation_rors(creators))

    def test_online_and_offline_findings_agree_on_feedback_cases(self):
        html = (ROOT / "app/checker.html").read_text()
        rules = html[html.index("const CANNED="):html.index("function human(")]
        cases = [
            [[{"name": "University", "id": "01kq0pv72"}], [{"name": "University"}]],
            [[{"name": "University"}], [{"name": "University"}]],
            [[{"name": "University", "id": "01kq0pv72"}],
             [{"name": "University", "id": "04h9pn542"}], [{"name": "University"}]],
            [[{"name": "University", "id": " "}]],
        ]
        for case in cases:
            with self.subTest(affiliations=case):
                record, expected = self.result(case)
                code = rules + "\nprocess.stdout.write(JSON.stringify(verdict(" + json.dumps(record) + ")));"
                run = subprocess.run(["node"], input=code, text=True, capture_output=True, check=True)
                actual = json.loads(run.stdout)
                self.assertEqual([f["key"] for f in actual["findings"]],
                                 [f["key"] for f in expected["findings"] if f["key"] != "REVIEW"])
                self.assertEqual(len(actual["eyeball"]), len([f for f in expected["findings"] if f["key"] == "REVIEW"]))
                if actual["eyeball"]:
                    self.assertNotIn(deterministic.canned("af2"), actual["message"])


if __name__ == "__main__":
    unittest.main()
