# Demo: moderation pass on the mock Archive

Six realistic submissions sit in the mock queue. The authors are fictional, but the
topics, file types, sizes, and affiliation styles follow real records published on
archive.materialscloud.org (2D magnet screening, MoS2 contacts, a GeSbTe machine-learned
potential, perovskite phonons, STM imaging). Each one shows a different outcome.

## Setup

The mock and the dashboard, already running:

```bash
python3 backend/mock_archive.py                          # mock Archive on :8899
MC_ARCHIVE=http://127.0.0.1:8899 python3 app/serve.py    # dashboard on :8000
```

To reset and re-seed:

```bash
rm backend/state.json
for f in fixtures/demo/*.json; do python3 scripts/submit_test_record.py --file "$f"; done
```

## The six records

| record | title | what it shows |
|---|---|---|
| `demo_01_clean` | High-throughput screening of 2D magnets | Accept. No findings at all. The tool still never clicks accept; the human does. |
| `demo_02_metadata_fixes` | Defect-tolerant carrier injection in MoS2 | Comment. Six canned fixes (title case, numbered affiliation, no address, two keywords, thin reference, no README) plus two human checks on the `.vasp` files. |
| `demo_03_doi_https` | Anharmonic phonon transport in halide perovskites | Trivial fix. The DOI was pasted as `https://doi.org/...`. This is the one thing the moderator normally edits quietly; the tool only proposes it. |
| `demo_04_big_waiver` | Crystallization kinetics of Ge-rich phase-change alloys | Over budget but waived. 28.5 GB against a 5 GB limit, with NCCR MARVEL funding, so accept with an exception. |
| `demo_05_big_escalate` | Ab initio database of adsorption energies | Escalate. 34.2 GB, unknown authors, no covered funder. Goes to Giovanni; the tool does not decide. |
| `demo_06_experimental` | Scanning tunneling microscopy of pentacene on Cu(111) | Scope call. Pure STM/AFM work in a closed format. The model judges the scope and drafts the canned Zenodo redirect (`r2`); the human declines. |

## Walkthrough (about five minutes)

1. Show the queue.

   ```bash
   python3 scripts/fetch_queue.py
   ```

2. Run the deterministic pass.

   ```bash
   python3 scripts/build_review.py
   open review.md
   ```

   Read `review.md` top to bottom. It follows the table order above: clean accept, the
   six-fix comment, the DOI fix, the waived size, the escalation, and the experimental
   record whose scope call a script cannot make.

3. Add the two soft judgements by hand.

   Open `review.md` and for each record write in the scope call and the description
   shape. On `demo_06` the deterministic findings were only "short description, no
   README, closed format"; the decline-for-scope verdict and the `r2` reply are that
   judgement. On `demo_05` the answer is escalate, not a decision.

4. Post one draft comment (mock only). Show the guards first:

   ```bash
   python3 scripts/post_comment.py 21f7673c runs/2026-07-10/a97d4-76790/comment.md
   MC_READ_ONLY=0 python3 scripts/post_comment.py 21f7673c runs/2026-07-10/a97d4-76790/comment.md --confirm
   curl -s http://127.0.0.1:8899/api/requests/21f7673c | python3 -m json.tool
   ```

   Request IDs change after a re-seed, so take the current ones from `review.md`.

5. Show the offline checker at `app/checker.html`. Double-click it, or deep-link a state
   with `checker.html?ex=needs%20fixes`. The served dashboard at
   http://127.0.0.1:8000 is the main workflow. In the checker, the example chips under
   the paste box are *clean, needs fixes, DOI as URL, too big waived, too big escalate,
   experimental*. Each one renders the record like an Archive page: badges, authors,
   abstract, keywords, funding, references, the files table with the size meter, and the
   problems tagged in red on the field they belong to. The right rail lists the findings
   (click to jump to the field) and shows the draft reply as a comment bubble, assembled
   from the canned lines. The scope replies (`r1` to `r4`) sit behind a "human judgement
   only" fold.

6. AI review, served version only. Once a provider is saved in Settings (OpenRouter's
   free model needs nothing but a key), it starts automatically with each check. The
   deterministic verdict renders right away; the AI card works in the background for
   around 15 to 60 seconds and comes back with the two judgements a script cannot make:
   scope and description shape. If it finds description issues, the matching canned lines
   are drafted into the reply bubble and labelled in the card. If it doubts the scope, it
   surfaces the canned scope reply with its own copy button. The important guard: the
   model only selects canned keys. `serve.py` looks the text up in `rules/canned.json`
   itself, so no model-written sentence can reach a submitter. The UI never names the
   model. The experimental example is the showpiece.

## Talking points

- The tool never accepts or declines. Publishing is irreversible even for staff, so the
  final click stays human, in the Archive web UI. There is no code path for it.
- Every sentence sent to a submitter is canned, taken from the approved replies in
  `rules/canned.json`. The model never writes one.
- Read-only by default. `MC_READ_ONLY=1` unless it is explicitly flipped, and posting
  needs `--confirm` on top of that. The cron job stays read-only.
- Only two grounds for rejection: out of scope, or over budget. Both are waived by a
  covered funder (MARVEL, SNSF, ERC, CSCS, EPFL). Everything else is a fix-request
  comment.
- Pointing the same scripts at production is one environment variable (`MC_API_BASE`),
  which is exactly why the mock exists.
