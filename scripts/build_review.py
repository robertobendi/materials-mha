#!/usr/bin/env python3
"""Run the deterministic pass over the open queue. Writes runs/<date>/ and review.md.

Two calls stay manual after this: is the description abstract-shaped, and is the
work computational. Fill those in per record and tidy comment.md before posting.
"""
import datetime, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mc_api, deterministic

ROOT = os.path.join(HERE, "..")
day = datetime.date.today().isoformat()
outdir = os.path.join(ROOT, "runs", day)
os.makedirs(outdir, exist_ok=True)

lines = [f"# Moderation review {day}\n"]
queue = mc_api.open_requests()
if not queue:
    lines.append("_Queue empty._\n")

for q in queue:
    rid = q["topic"]["record"]
    rec = mc_api.draft(rid)
    entries = mc_api.files(rid)
    res = deterministic.check(rec, entries)
    d = os.path.join(outdir, rid)
    os.makedirs(d, exist_ok=True)
    json.dump(rec, open(f"{d}/record.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    json.dump(res, open(f"{d}/findings.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    hard = [f for f in res["findings"] if f["key"] not in ("REVIEW",)]
    soft = [f for f in res["findings"] if f["key"] == "REVIEW"]
    seen, uniq = set(), []
    for f in hard:
        if f["text"] not in seen:
            seen.add(f["text"]); uniq.append(f["text"])
    comment = "\n".join(uniq)
    open(f"{d}/comment.md", "w", encoding="utf-8").write(comment + "\n" if comment else "")

    size_issue = any(f["key"] == "SIZE" for f in hard)
    if not hard and not soft:
        action = "**accept** (clean; confirm the work is computational, then accept in the web UI)"
    elif not hard and soft:
        action = "**human review** - resolve the checks below before deciding"
    elif size_issue and res["funding_waiver"]:
        action = "**accept with exception** - over budget, but funded by " + ", ".join(res["funders"])
    elif size_issue:
        action = "**escalate** - over budget, no covered funder. Ask to shrink, or ask Giovanni."
    else:
        action = "**review draft** - verify each finding before posting, do not accept"

    lines += [f"\n## {rec['metadata']['title']}",
              f"`{rid}` · request `{q['id']}` · {res['total_bytes']/1e9:.2f} GB"
              f"{' · AiiDA' if res['is_aiida'] else ''}"
              f"{' · funder waiver' if res['funding_waiver'] else ''}",
              f"\nProposed action: {action}\n"]
    if hard:
        lines.append("**Findings**\n")
        lines += [f"- `{f['key']}` {f.get('detail') or ''}" for f in hard]
    if soft:
        lines.append("\n**Needs your eyes**\n")
        lines += [f"- {f['text']}" for f in soft]
    if comment:
        lines += ["\n<details><summary>Draft comment</summary>\n", "```", comment, "```", "\n</details>"]
    lines.append(f"\nPost with: `python3 scripts/post_comment.py {q['id']} runs/{day}/{rid}/comment.md --confirm`\n")

open(os.path.join(ROOT, "review.md"), "w", encoding="utf-8").write("\n".join(lines))
print(f"{len(queue)} record(s) -> review.md and runs/{day}/")
