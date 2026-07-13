# CLAUDE.md

Guidance for coding agents working on this repository.

## What this is

tex-review: a local, stdlib-only tool for reviewing agent-proposed edits
to LaTeX manuscripts. An agent emits suggestions as JSON (it never edits
the `.tex` files); a human accepts/rejects them in a web UI; the tool
applies only the accepted ones. Edits are anchored on **exact text**,
not line numbers or git hunks, so multiple independent changes on the
same line coexist.

## Files

Installable package (flit, `pyproject.toml`, console script
`tex-review`, also `python -m texreview`):

- `texreview/core.py` — Store (loading, dedupe, decisions), anchor
  matching, apply engine.
- `texreview/server.py` — HTTP server (stdlib `http.server`) + `serve()`.
- `texreview/cli.py` — argparse CLI: `review` (alias `serve`), `apply`,
  `check`, `instruct` (prints agent prompt + JSON schema).
- `texreview/static/index.html` — the whole UI, single file, vanilla
  JS, no build step. Loaded via `importlib.resources`.
- `texreview/instructions.md` — canonical self-contained prompt for a
  suggesting agent; `texreview/schema.json` — JSON Schema for pass
  files. Both printed by `tex-review instruct`; keep in sync with the
  schema if it changes.
- `review.py` — thin compatibility shim so `python review.py ...`
  still works from a checkout.
- `README.md` — user-facing docs and schema.
- `example/` — sample manuscript (`paper.tex`) + `review/` directory
  with two agent passes. Fixture for manual testing.
- `PLAN.md` — roadmap (tests, UI features, publishing).

## Hard constraints

- **Stdlib only.** No pip dependencies; the tool must run anywhere
  Python (3.9+) runs.
- **Single-file UI**, vanilla JS, no build step.
- Server binds localhost only.
- Single-file mode (`suggestions.json`) must keep working.
- Agent pass files (`review/NNNN-*.json`) are strictly read-only to
  this tool — never written, never rewritten.

## Two storage modes

1. **Directory mode (primary):** serve a `review/` dir. Each
   `NNNN-name.json` is one agent pass. Human decisions live in
   `review/decisions.json`, a map `content_key -> status`, where
   `content_key = sha1(file, old, new, occurrence)[:12]`. Consequences
   to preserve: decisions survive re-emitted suggestions; identical
   suggestions across passes are deduped (first wins); deleting a pass
   file is a valid operation. The dir is re-scanned on every
   `GET /api/state` (hot reload while an agent keeps working).
2. **Single-file mode (legacy):** serve one `suggestions.json`;
   statuses are written back into it.

## Suggestion schema (per item)

`file` (relative to `base_dir`, default `..` from the pass file),
`old` (verbatim anchor), `new`, optional `occurrence` (1-based),
`reasoning`, `tags`, `id`. Anchor resolution states: `ok`, `missing`,
`ambiguous` (multi-match without `occurrence`), `overlap` (two
*accepted* spans intersect), `bad-file`.

## Apply semantics (invariants — do not break)

- Only `accepted` + `ok` suggestions are applied; everything else is
  reported as skipped, never silently applied.
- Within a file, edits apply **right-to-left by offset** so positions
  never shift; a final `text[a:b] == old` check guards against races.
- One-time `<file>.bak` backup per file.
- Applied items become status `applied` (in `decisions.json` or the
  single file). Agent pass files are never written.
- All JSON writes are tmp-file + atomic replace.

## HTTP API

- `GET /` → `index.html`; `GET /mathjax/*` → optional local MathJax
  (path-traversal guarded), 404 lets the page fall back to CDN.
- `GET /api/state?ctx=N` → `{source, suggestions: [{..., match}]}`
  (reloads; `ctx` = context lines either side of each anchor,
  default 1, clamped 0–99).
- `GET /api/file?id=<sid>` → `{file, text}`: full text of the file the
  suggestion points at, resolved through the store (no client paths).
- `POST /api/decide {id, status}` (status ∈ pending/accepted/rejected).
- `POST /api/apply` → `{applied: [...], skipped: [{id, reason}]}`.

## UI notes

- Keyboard: `j/k` move, `a/r` decide + auto-advance, `u` undo, `m`
  toggle math, `d` document pane, `+/-` context lines, `A` apply.
  No shortcut legend in the toolbar: keys are shown as `<kbd>` badges
  on the buttons they trigger (`a/r/u` + a `j/k` hint only on the
  selected card; `A`, `m`, `d`, `+/−` on their controls). A round `?`
  button (or the `?` key) opens a native `<dialog>` with the overview
  and full shortcut table; Esc/backdrop-click closes it, and other
  shortcuts are ignored while it is open.
  Filters:
  all/pending/accepted/rejected, plus tag chips in the toolbar (click
  to filter by tag, click again to clear; card tag chips work too;
  combines with the status filter). The `± N lines` toolbar input sets
  diff context (persisted in localStorage as `texreview.ctx`).
- Word-level diff = LCS over whitespace-preserving tokens (JS, capped;
  falls back to plain del/ins blocks on huge edits).
- Document pane (`⧉ Doc` / `d`, persisted as `texreview.doc`, hidden
  under 1100px): sticky side `<pre>` showing the selected suggestion's
  whole file verbatim with line numbers; all suggestion spans in that
  file are `<mark>`ed by status, selection syncs both ways (card ↔
  mark click), refetched when state reloads. Pane-header buttons:
  `wrap`/`no-wrap` (horizontal scroll) and `⇄ side` (left/right),
  both persisted (`texreview.wrap`, `texreview.docside`). Each line
  is a block-level `.ln` span with an absolutely-positioned gutter
  number, so wrapped continuations indent past the gutter; marks are
  split at newlines so they never cross `.ln` boundaries. Plain text by design —
  see PLAN.md before adding a highlighter.
- MathJax (tex-svg) typesets **only** margin notes and the rendered
  before/after previews via targeted `typesetPromise` calls
  (`startup.typeset: false`). The diff block is verbatim source — the
  exact bytes that get applied — and must never be typeset.

## How to verify changes

```
pip install .                                # flit_core build backend
tex-review check example/review              # anchors resolve
tex-review review example/review             # manual UI pass
cp -r example /tmp/ex && tex-review review /tmp/ex/review
# accept a few (incl. s002+s003: same line), apply, inspect paper.tex,
# confirm example pass files are byte-identical and .bak exists
```

There is no automated test suite yet (planned — see PLAN.md); do the
manual pass above after touching the apply engine or matching logic.
