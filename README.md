# Materials MHA

A moderation helper for the [Materials Cloud Archive](https://archive.materialscloud.org).
It reads the open submission queue, runs the metadata and file-format checks I use
every day, keeps the conversation history in view, and drafts a reply out of the
approved wording in `rules/canned.json`.

It is deliberately careful about what it is allowed to do. It reads, it suggests,
and after an explicit confirmation it can post one comment. It cannot accept,
decline, edit, or publish a record. That last click stays with the human, in the
Archive web UI.

I built this while moderating the `mcarchive` community. The rules in `rules/` come
from that work, including a look back at how the previous moderator handled about a
hundred accepted records.

## What it does

- Dashboard of the open queue with counts for new submissions, author replies, and
  records waiting on an author.
- Deterministic checks on title, affiliations, description, keywords, resource type,
  references, README, file formats, and total size.
- An optional AI review that reads a small projection of the record and says whether
  the work looks computational and whether the description reads like an abstract.
  It only picks from the canned reply keys; it never writes a sentence itself.
- Reply draft grouped by record field, assembled from the canned wording.
- Guarded sending: preview, typed request ID, explicit acknowledgment, and a fresh
  read of the record and timeline right before the comment goes out.
- A read-only approval handoff for clean records. It re-reads everything, then opens
  the record in the Archive so the moderator can make the final call.
- Open, accepted, and declined history, plus an in-app copy of the playbook.

## Running it

There is nothing to install. Python 3.8+ and no third-party packages.

```powershell
python app\serve.py --open
```

The workspace comes up at `http://127.0.0.1:8000`. Put the moderator token in the
environment or in a local `.env` file:

```text
MC_TOKEN=your-token-here
```

`.env` is git-ignored. The server holds the token in memory only, never sends it back
to the page, and only attaches it to a small set of GET routes aimed at
`archive.materialscloud.org`. Redirects are blocked so the token cannot leak to
another host.

To try it without touching production, run the mock Archive against the bundled demo
fixtures:

```powershell
python backend\mock_archive.py
$env:MC_API_BASE = "http://127.0.0.1:8899"
$env:MC_ARCHIVE = "http://127.0.0.1:8899"
python app\serve.py --open
```

See `DEMO.md` for a short walkthrough of the six example submissions.

The offline checker at `app/checker.html` works for pasted JSON with no server at all,
and a copy is hosted on GitHub Pages at
<https://robertobendi.github.io/materials-mha/app/checker.html>. Pages is static, so the
dashboard and the guarded comment flow still need the local server.

## AI provider

Open **Settings**, pick a provider, and paste a key. OpenRouter, DeepSeek, OpenAI,
and any OpenAI-compatible endpoint are supported. OpenRouter routes to a free model
by default. For the others, leave the model field empty and the first chat model the
endpoint lists is used, or type a model ID yourself.

A deployment can configure the default from the environment instead:

```text
MC_AI_PROVIDER=openrouter      # openrouter | deepseek | openai | custom
MC_AI_KEY=sk-or-...
MC_AI_MODEL=                   # blank = auto
MC_AI_ENDPOINT=                # custom provider only
```

Provider settings are per-session and are gone when the server stops or the session
expires. The key lives in server memory and is sent as a bearer token straight to the
configured `/chat/completions` endpoint. Remote endpoints must use HTTPS; plain HTTP
is only allowed for localhost. Redirects are blocked. Leave the key field empty when
saving to keep an already stored key.

The model sees the title, description, keywords, references, funders, filenames, and
the deterministic findings. It never sees the Archive token or the conversation. The
reply is requested as JSON, checked against a schema, and then trimmed down to the
advisory fields. Whatever the model says, the rules and the human checks still apply.

## Configuration

| variable | purpose |
|---|---|
| `MC_API_BASE` | Archive to talk to. Mock is `http://127.0.0.1:8899`, production is the default. |
| `MC_TOKEN` | Moderator token. Only used against production. |
| `MC_READ_ONLY` | `1` by default. Posting requires `0` and `--confirm`. |
| `MC_HOST` | Interface to bind. Defaults to `127.0.0.1`. |
| `PORT` | Port to bind. Defaults to `8000`. |
| `MC_ARCHIVE` | Archive the dashboard talks to. Defaults to production. |
| `MC_ALLOWED_HOSTS` | Comma-separated host allowlist when running behind a proxy. |
| `MC_ALLOW_ENV_TOKEN` | Set to `1` to load `MC_TOKEN` on a non-loopback bind. Off by default. |
| `MC_DISABLE_COMMENTS` | Set to `1` to make the workspace read-only. |

## Hosting

No dependencies and no build step, so it runs anywhere Python does. There is a
`Dockerfile` for container hosts and a `render.yaml` blueprint for Render.

```bash
docker build -t materials-mha .
docker run -p 8000:8000 -e MC_AI_PROVIDER=openrouter -e MC_AI_KEY=sk-or-... materials-mha
```

On a non-loopback bind the shared token is not loaded; each visitor connects their own
moderator token in the browser, so only people with Archive access can read anything.
Set `MC_ALLOW_ENV_TOKEN=1` only for a private instance you control, and pin
`MC_ALLOWED_HOSTS` when running behind a proxy.

## The command-line pass

The dashboard is the main workflow, but the checks also run headless. This writes
`runs/<date>/` and a digest at `review.md`:

```powershell
python scripts\build_review.py
```

Each record still needs the two calls a script cannot make: is the work computational,
and does the description read like an abstract. Add those verdicts to `review.md`, then
post with the guard in place:

```powershell
$env:MC_READ_ONLY = "0"
python scripts\post_comment.py <request_id> runs\<date>\<record>\comment.md --confirm
```

`scripts/calibrate_history.py` does a read-only audit of past decisions and prints
aggregate counts. It never stores record text, request IDs, conversation text, or
credentials.

## Tests

```powershell
python -m unittest discover -s tests -v
node --check app\app.js
```

## Layout

```text
app/index.html            dashboard shell
app/app.css               dashboard styling
app/app.js                queue, review, history, and handoff UI
app/serve.py              localhost server and safety gates
app/ai_review.py          advisory review for OpenAI-compatible providers
app/archive_client.py     read-only Archive client and allowlist
app/dashboard.py          queue projections and reply composition
app/checker.html          offline checker for pasted JSON
scripts/deterministic.py  metadata and file rules
scripts/build_review.py   headless pass over the open queue
scripts/calibrate_history.py  read-only historical audit
rules/canned.json         approved reply wording
rules/formats.json        file-format and size policy
rules/precedents.json     playbook from past moderation
backend/mock_archive.py   local test Archive
```

## License

MIT. See `LICENSE`.

Written and maintained by Roberto Bendinelli.
