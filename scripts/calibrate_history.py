#!/usr/bin/env python3
"""Compare current rules with past moderator decisions using GET requests only.

The script prints aggregate counts. It never stores record metadata, request IDs,
conversation text, or credentials and cannot call a write endpoint.
"""
import argparse
import collections
import html
import json
import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "scripts"))
from archive_client import Archive, PRODUCTION
import deterministic


COMMUNITY_ID = "30074283-2e48-4526-b2df-c3c2191db3cb"


def local_token():
    token = os.environ.get("MC_TOKEN", "")
    path = ROOT / ".env"
    if not token and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("MC_TOKEN="):
                token = line.split("=", 1)[1].strip()
                break
    if not token:
        raise SystemExit("MC_TOKEN is missing; set it or add it to the ignored .env file.")
    if len(token) > 4096 or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise SystemExit("MC_TOKEN is invalid; use a token without spaces or line breaks.")
    return token


def accepted_by(events, moderator_id):
    return any(event.get("type") == "L"
               and str(event.get("created_by", {}).get("user")) == moderator_id
               and event.get("payload", {}).get("event") == "accepted"
               for event in events)


def moderator_comments(events, moderator_id):
    return [str(event.get("payload", {}).get("content") or "") for event in events
            if event.get("type") == "C"
            and str(event.get("created_by", {}).get("user")) == moderator_id]


def terminal_shape(description):
    """Return a non-content description of the final visible character."""
    visible = html.unescape(re.sub(r"<[^>]+>", " ", str(description or ""))).strip()
    if not visible:
        return "empty"
    terminal = visible[-1]
    names = {
        ".": "period", "!": "exclamation", "?": "question_mark",
        ":": "colon", ";": "semicolon", ",": "comma",
        ")": "closing_parenthesis", "]": "closing_bracket",
        "\"": "closing_quote", "'": "closing_apostrophe",
    }
    if terminal in names:
        return names[terminal]
    if terminal.isdigit():
        return "digit"
    if terminal.isalpha():
        return "letter"
    return "other"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--moderator-id", default="1965")
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 100:
        raise SystemExit("--limit must be between 1 and 100")

    archive = Archive(PRODUCTION, local_token())
    query = (f"receiver.community:{COMMUNITY_ID} AND type:community-submission "
             "AND status:accepted")
    requests = archive.requests(query, size=100)["hits"]
    examined = analyzed = 0
    finding_counts = collections.Counter()
    records_by_finding = collections.Counter()
    finding_version_shapes = collections.Counter()
    records_with_hard_findings = 0
    records_with_human_checks = 0
    comment_count = 0
    style = collections.Counter()
    preprint_shapes = collections.Counter()
    preprint_link_fields = collections.Counter()
    description_endings = collections.Counter()
    reference_shapes = collections.Counter()
    thin_reference_field_shapes = collections.Counter()
    missing_readme_shapes = collections.Counter()
    affiliation_numbering_shapes = collections.Counter()
    resource_type_file_shapes = collections.Counter()
    blocked_extension_shapes = collections.Counter()
    size_exception_shapes = collections.Counter()

    for request in requests:
        examined += 1
        request_id = str(request.get("id") or "")
        events = archive.timeline(request_id)["hits"]
        if not accepted_by(events, args.moderator_id):
            continue
        record_id = str(request.get("topic", {}).get("record") or "")
        record = archive.record(record_id, draft=False)
        entries = record["files"]["entries"]
        if isinstance(entries, dict):
            entries = [dict(value, key=key) for key, value in entries.items()]
        result = deterministic.check(record, entries)
        hard = [finding for finding in result["findings"] if finding["key"] != "REVIEW"]
        soft = [finding for finding in result["findings"] if finding["key"] == "REVIEW"]
        effective_hard = [finding for finding in hard
                          if finding["key"] != "SIZE" or not result["funding_waiver"]]
        if effective_hard:
            records_with_hard_findings += 1
        if soft:
            records_with_human_checks += 1
        finding_counts.update(finding["key"] for finding in effective_hard)
        hard_keys = {finding["key"] for finding in effective_hard}
        records_by_finding.update(hard_keys)
        version = record.get("versions", {}).get("index")
        version_shape = "first_version" if version in (None, 1) else "later_version"
        finding_version_shapes.update(f"{key}:{version_shape}" for key in hard_keys)
        if "d8" in hard_keys:
            description_endings.update([
                terminal_shape(record.get("metadata", {}).get("description"))])
        for finding in effective_hard:
            if finding["key"] == "ref2":
                kind = "missing" if finding.get("detail") == "no reference at all" else "thin"
                reference_shapes.update([kind])
                if kind == "thin":
                    for reference in record.get("custom_fields", {}).get("mc_references", []):
                        citation = str(reference.get("ref_citation") or "")
                        if deterministic.is_thin_reference(reference):
                            populated = sorted(key for key, value in reference.items() if value)
                            thin_reference_field_shapes.update(["+".join(populated)])
        if "f1" in hard_keys:
            file_names = [entry.get("key", "") for entry in entries]
            archived = any(deterministic.ext_of(name) in (".zip", ".tar.gz", ".tar.bz2")
                           for name in file_names)
            missing_readme_shapes.update([
                "archive_present" if archived else "no_archive",
                "single_file" if len(file_names) == 1 else "multiple_files",
            ])
        if "af1" in hard_keys:
            for finding in effective_hard:
                if finding["key"] == "af1":
                    marker = re.match(r"^\s*\d+([.)])", str(finding.get("detail") or ""))
                    affiliation_numbering_shapes.update([
                        "period_marker" if marker and marker.group(1) == "." else "parenthesis_marker"])
        if "rt1" in hard_keys:
            file_names = [entry.get("key", "") for entry in entries]
            resource_type_file_shapes.update([
                "archive_present" if any(deterministic.ext_of(name) in
                                         (".zip", ".tar.gz", ".tar.bz2")
                                         for name in file_names) else "no_archive",
                "top_level_code" if any(deterministic.ext_of(name) in
                                        (".py", ".sh", ".ipynb")
                                        for name in file_names) else "no_top_level_code",
            ])
        if "f7" in hard_keys:
            blocked_extension_shapes.update(
                deterministic.ext_of(str(finding.get("detail") or "").split(":", 1)[0])
                for finding in effective_hard if finding["key"] == "f7")
        if any(finding["key"] == "SIZE" for finding in hard):
            size_exception_shapes.update([
                "covered_funder" if result["funding_waiver"] else "no_covered_funder"])
        for reference in record.get("custom_fields", {}).get("mc_references", []):
            if deterministic.is_preprint_without_link(reference):
                preprint_shapes.update(["+".join(sorted(reference))])
                preprint_link_fields.update(
                    key for key, value in reference.items()
                    if value and any(term in key.lower() for term in ("doi", "url", "link")))
        for text in moderator_comments(events, args.moderator_id):
            comment_count += 1
            lower = text.lower()
            style["thanks_for_submission"] += "thank you for your submission" in lower
            style["grouped_field_headings"] += any(
                f">{heading}<" in lower for heading in
                ("title", "files", "description", "references", "affiliations", "keywords"))
            style["conversation_call_to_action"] += "conversation area" in lower
            style["best_regards"] += "best regards" in lower
            style["acknowledges_changes"] += "changes you made" in lower
        analyzed += 1
        if analyzed >= args.limit:
            break

    print(json.dumps({
        "safety": "GET-only; aggregate output; no private corpus stored",
        "moderator_user_id": args.moderator_id,
        "requests_examined": examined,
        "records_analyzed": analyzed,
        "accepted_records_flagged_by_current_hard_rules": records_with_hard_findings,
        "accepted_records_with_human_checks": records_with_human_checks,
        "findings_on_accepted_records": dict(finding_counts.most_common()),
        "accepted_records_by_finding": dict(records_by_finding.most_common()),
        "finding_version_shapes": dict(finding_version_shapes.most_common()),
        "preprint_reference_shapes": dict(preprint_shapes.most_common()),
        "nonempty_structured_link_fields_on_flagged_preprints": dict(preprint_link_fields.most_common()),
        "description_terminal_shapes_for_d8": dict(description_endings.most_common()),
        "reference_shapes_for_ref2": dict(reference_shapes.most_common()),
        "thin_reference_field_shapes": dict(thin_reference_field_shapes.most_common()),
        "missing_readme_shapes_for_f1": dict(missing_readme_shapes.most_common()),
        "affiliation_numbering_shapes_for_af1": dict(affiliation_numbering_shapes.most_common()),
        "resource_type_file_shapes_for_rt1": dict(resource_type_file_shapes.most_common()),
        "blocked_extension_shapes_for_f7": dict(blocked_extension_shapes.most_common()),
        "size_exception_shapes": dict(size_exception_shapes.most_common()),
        "moderator_comments": comment_count,
        "style_signals": dict(style),
    }, indent=2))


if __name__ == "__main__":
    main()
