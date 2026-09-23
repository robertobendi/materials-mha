# Maintaining Materials MHA

For whoever takes this over. `README.md` explains how to run the tool; this page
explains how it fits together and how to change it without breaking the safety rules.

## The rules that must not change

1. **The tool never accepts, declines, or edits a record.** Publishing is irreversible,
   even for staff. There is no `accept()` anywhere, on purpose. Don't add one.
2. **Every sentence sent to a submitter comes from `rules/canned.json`**, wrapped in the
   opening/closing from `rules/precedents.json`. The AI only picks keys; it never writes
   reply text.
3. **Posting a comment is the only write**, and it goes through `CommentWriter` in
   `app/archive_client.py` after a typed confirmation. `scripts/post_comment.py` needs
   both `MC_READ_ONLY=0` and `--confirm`.
4. **No reminders to submitters.** If they don't reply, the record waits.

## How a review flows

```text
Archive API ──► archive_client.py ──► deterministic.py ──► findings (each has a canned key)
 (read-only)     (GET allowlist,        (pure rules, no AI)        │
                  no redirects)                                     ▼
                                     dashboard.compose_reply ──► draft reply
                                     (opening + grouped canned lines + closing)
                                                                    │
                     optional: ai_review.py picks extra canned keys ┤
                                                                    ▼
                                         human edits ──► CommentWriter.post (one comment)
```

Two front ends share the same rules:

- **Web workspace** (`app/serve.py`, Flask): dashboard, review, AI, guarded send.
- **Command line** (`scripts/build_review.py`): writes `review.md` and
  `runs/<date>/<record>/comment.md` for the whole queue. Standard library only, so it
  can run from cron with `MC_READ_ONLY=1`.

## Where things live

| You want to change... | Edit |
|---|---|
| Reply wording | `rules/canned.json` (and the copy in `app/checker.html`, see below) |
| Greeting / sign-off | `rules/precedents.json` → `templates` |
| Order of field headings in a reply | `rules/precedents.json` → `section_order`; key prefixes map to headings in `SECTION_BY_KEY` in `app/dashboard.py` |
| Fixes the moderator makes instead of the author | `rules/precedents.json` → `moderator_only_finding_keys` |
| Allowed / blocked file formats, size limits, waiver funders | `rules/formats.json` |
| A metadata check | `scripts/deterministic.py` → `check()` |
| A web endpoint | `app/serve.py` (one function per route) |
| Which Archive URLs may be read | `Archive._safe_get_path` in `app/archive_client.py` |

## Common tasks

### Add a new canned reply

1. Add the key and text to the right section of `rules/canned.json`. Keys are prefixed
   by field (`t` title, `af` affiliations, `d` description, `f` files, `ref` references,
   `k` keywords, ...); the prefix decides which heading it lands under in the reply.
2. Copy it into the `CANNED` object in `app/checker.html`. That offline page is a single
   self-contained file and does not read `canned.json`. (They are already slightly out
   of sync: `ref7` is patched in by hand below `CANNED`, and `f6` exists only there.)
3. Only use wording the moderation team has approved.

### Add a new check

1. In `scripts/deterministic.py`, inside `check()`, call `add("<canned key>", detail)`
   when the problem is certain. If a human has to look, append a finding with key
   `"REVIEW"` and text starting `"Human check: "`. Those are shown to the moderator and
   never sent.
2. Mirror the rule in `rules()` in `app/checker.html`.
3. Add a test in `tests/`. Build the record from `fixtures/base_clean.json`, as the
   existing tests do. When the rule is in both places, add a test that runs the
   `checker.html` rules with `node` (see `test_offline_checker_*`).

### Test against real record shapes

The fixtures are simpler than production. Before trusting a new check, fetch a real
published record (read-only, no token needed) and run the rules on it:

```bash
curl -s https://archive.materialscloud.org/api/records/<id> > /tmp/rec.json
python3 -c "
import sys, json; sys.path.insert(0, 'scripts'); import deterministic as d
r = json.load(open('/tmp/rec.json'))
print(json.dumps(d.check(r, [dict(v, key=k) for k, v in r['files']['entries'].items()]), indent=1))"
```

Known gotchas from production data:

- `custom_fields.mc_references[].ref_link` is an object
  `{"ref_scheme": "doi", "ref_identifier": "..."}`, not a string.
- The same DOI also appears in `metadata.related_identifiers`.
- `metadata.description` is raw HTML, sometimes pasted from an editor
  (`<pre><span>...`), which renders in a monospace font.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
node --check app/app.js
```

`tests/test_workspace.py` starts the Flask app and a fake Archive on random ports and
talks to them over real HTTP, so it covers the security checks (host, origin, CSRF),
the send guards, and the error messages.

## Dependencies

- `app/` needs Flask (`requirements.txt`). Install into `.venv`; the macOS launcher
  `Open Review.command` does this on first run.
- `scripts/` and `backend/mock_archive.py` use only the standard library. Keep it that
  way so the cron pass has nothing to install.
