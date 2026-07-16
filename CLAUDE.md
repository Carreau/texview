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
  `check`, `purge` (remove resolved suggestions; `--dry-run`,
  `--status`), `instruct` (prints agent prompt + JSON schema).
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
- **Single-file UI**, vanilla JS, no build step. Browser libraries
  (MathJax, PDF.js) are vendored in the package
  (`texreview/static/{mathjax,pdfjs}/`) and served from `/mathjax/`,
  `/pdfjs/` — a working-directory copy overrides, the CDN is the
  last-resort fallback (used by the statically hosted page), and the
  UI degrades gracefully if none load. PDF.js must stay same-origin:
  browsers reject cross-origin workers (the CDN path fetches the
  worker and feeds it in as a blob URL).
- Server binds localhost only.
- Single-file mode (`suggestions.json`) must keep working.
- Agent pass files (`review/NNNN-*.json`) are strictly read-only to
  this tool — never written, never rewritten. Single deliberate
  exception: the explicitly human-invoked `tex-review purge`, which
  removes resolved suggestions from pass files (and prunes
  decisions/comments); nothing in the review/serve/apply flow ever
  writes them.

## Three run modes

Server mode (the two storage layouts below), and **local mode**: the
same `index.html` served statically (GitHub Pages via
`.github/workflows/pages.yml`, or `file://`). On load, `detectMode()`
probes `/api/state`; without a server, every `api()` call is answered
by an in-page engine (`LOCAL-BACKEND` block) that mirrors the Python
core over files loaded via 📂 Open / drag-drop; ⬇ Save downloads the
edited `.tex` files + `decisions.json` / `manual.json` /
`comments.json`. Content keys and matching are byte-compatible with
Python — enforced by `tests/test_static_parity.py`, which extracts the
`LOCAL-CORE`/`LOCAL-BACKEND` marker blocks and runs them under node
against the Python core. If you change `content_key`, `locate`, apply
or decisions semantics, change BOTH implementations.

## Two storage modes

1. **Directory mode (primary):** serve a `review/` dir. Each
   `NNNN-name.json` is one agent pass. Human decisions live in
   `review/decisions.json`, a map `content_key -> status | {status,
   new?, reasoning?, tags?}` (the dict form holds human edits — they
   override pass content at load, keyed by the suggestion's original
   content key, so pass files stay read-only and edits survive
   re-emission). `content_key = sha1(file, old, new, occurrence)[:12]`
   over the *original* pass values; `old`/`occurrence` are identity
   and never editable.
   Human-created suggestions (UI "＋ suggest") go into
   `review/manual.json` — tool-owned like `decisions.json`, but loaded
   as a regular pass; agent pass files stay read-only.
   Replies (threaded comments per suggestion, `{text, author, date}`,
   authorship trusted not authenticated): human ones live in tool-owned
   `review/comments.json` keyed by content key; agents reply via a
   top-level `"replies": [{to: <id|key>, ...}]` array in a *new* pass
   file. Merged and date-sorted into `suggestion["replies"]` at load;
   suggestions may also carry optional `author`/`date` (manual ones are
   stamped with `getpass.getuser()` + UTC time). Consequences
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
- Apply records `applied_at` (the replacement's offset in the final
  text) alongside the status; `Store.revert` undoes an applied
  suggestion (`new` -> `old`) via that hint, falling back to a unique
  match of `new`, else fails with a reason (`ambiguous`/`missing`/
  `deleted-text` for `new == ""`). Revert sets status back to
  `pending` and clears `applied_at`. No new `.bak` — `.bak` stays the
  pre-apply snapshot.
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
- `POST /api/suggest {file, old, new, occurrence?, reasoning?, tags?}`
  → `{id}`: create a human-authored suggestion (validated: anchor must
  resolve `ok`; identical existing suggestion → `{id, existing: true}`).
  Stored via `Store.add_manual` (manual.json / the single file).
- `POST /api/reply {id, text, author?}` → append a reply (author
  defaults to the OS username; date stamped server-side).
- `POST /api/reply_edit {id, ci, text}` → edit a *human* reply (`ci` =
  its index in comments.json / the item's inline list, surfaced as
  `ci` + `editable: true` on the reply); empty text deletes it. Agent
  replies (from pass files) are not editable.
- `POST /api/edit {id, new?, reasoning?, tags?}`: human-edit any
  non-applied suggestion via `Store.edit` (decisions.json override in
  dir mode, in-place in single-file mode); sets `edited: true`.
- `POST /api/apply` → `{applied: [...], skipped: [{id, reason}]}`.
- `POST /api/revert {id}` → `{ok}`; 404 unknown, 409 not-applied or
  unrevertable (reason in `error`). UI: `↶ Revert` on applied cards.
- `GET /api/pdf?id=<sid>` → the compiled PDF next to the suggestion's
  tex file (`<stem>.pdf`, containment-guarded; `Cache-Control:
  max-age=300` so page jumps don't refetch).
- `POST /api/sync {id, pdf?}` → synctex forward search for the
  suggestion's current line (falls back to `applied_at` for applied
  ones). PDF resolution: explicit `pdf` (rel path, containment
  checked) > `<stem>.pdf` next to the tex file > a single PDF found
  under the root (`_pdf_candidates`, capped rglob skipping dotdirs) —
  handles `\input`/`\include` where only the root document has a PDF.
  Candidates are ranked (sibling `.synctex.gz` > sibling `.tex` >
  shallower path) and a unique synctex-bearing PDF is auto-picked, so
  `figures/*.pdf` never crowd out the root document. Responses carry
  `pdf` (chosen) + `pdfs` (candidates) for the pane's dropdown;
  ambiguous/no PDF → 404 with `pdfs`. `synctex_view` retries with
  path forms relative to the PDF's directory (synctex records inputs
  as TeX saw them) and returns an error *string* on failure — check
  `isinstance(loc, str)`.
- `--debug` (review subcommand) → `logging` logger `texreview`:
  request lines, ≥400 API responses, and every synctex invocation
  with its parsed result/stdout. With
  `--pdf-viewer TEMPLATE` (placeholders `{line} {tex} {pdf}`,
  shlex-split then formatted per token) it spawns the external viewer
  and returns `{mode: "viewer"}`; otherwise runs the `synctex` CLI and
  returns `{mode: "page", page, url}`. 404 no PDF, 409 synctex
  unavailable. `synctex` itself is mocked in tests (no TeX in CI).
- `POST /api/purge {statuses?}` → purge report (defaults
  applied+rejected). UI: `🧹 Purge` toolbar button (confirm dialog,
  flushes pending decisions first). In local mode, purged pass files
  are marked dirty and included in ⬇ Save.

## UI notes

- Keyboard: `j/k` move, `a/r` decide + auto-advance, `u` undo, `m`
  toggle math, `d` document pane, `+/-` context lines, `A` apply.
  `e` (or the card's `✎ Edit`) opens the same dialog as `＋ suggest`
  to edit replacement/tags/comment of any non-applied suggestion;
  edited cards get an `✎ edited` chip. The dialog offers every tag
  already in use as click-to-toggle chips. A floating `＋` follows the
  document-pane selection (in addition to the header button).
  No shortcut legend in the toolbar: keys are shown as `<kbd>` badges
  on the buttons they trigger (`a/r/u` + a `j/k` hint only on the
  selected card; `A`, `m`, `d`, `+/−` on their controls). A round `?`
  button (or the `?` key) opens a native `<dialog>` with the overview
  and full shortcut table; Esc/backdrop-click closes it, and other
  shortcuts are ignored while it is open.
  Filters:
  all/pending/accepted/rejected(+applied — that tab appears only when
  something is applied); counts live in the tab labels ("Pending (5)"),
  set by render(), which owns the filter tabs' on-state (the click
  handler only matches `.tab[data-f]` — don't widen it, other toolbar
  buttons share the .tab class). Plus tag chips in the toolbar (click
  to filter by tag, click again to clear; card tag chips work too;
  `✓/✕ shown` buttons appear when a filter narrows the list and
  bulk-decide the visible pending items — the counterpart of the
  pattern tags `instruct` asks agents to emit for recurring issues) and
  a per-file `<select>` (keyed by `fkey`, hidden when only one file
  has suggestions; choosing a file moves the selection — and the doc
  pane — into it). Status ∩ tag ∩ file all combine.
- PDF pane (`📄 PDF` / `P`, persisted as `texreview.pdf`, hidden in
  local mode and under 1100px): PDF.js (served from the package via
  `/pdfjs/`, then CDN) renders a scrollable stack — one `.pdfpage`
  placeholder per page, lazily rendered via IntersectionObserver
  (60% rootMargin), far pages evicted past 16 rendered, page
  indicator in the header, rebuilt on resize. Sync scrolls to the
  target page's offset + exact synctex y (points from page top ×
  scale) with the `#pdfmark` highlight bar; the document is cached
  per `pdfkey` and renders are sequence-guarded. If PDF.js
  fails to load, degrades to the built-in viewer iframe via
  `/api/pdf?id=...&v=<page>#page=<page>`. Selection changes call
  `/api/sync` debounced 250 ms; in `--pdf-viewer` mode the pane just
  reports "→ external viewer".
- `▤ notes` toolbar toggle (persisted `texreview.notes`): moves the
  reviewer note below the diff (`body.noteunder` collapses the card
  grid to one column) instead of the margin column.
- Panel order: `ORDER` (`texreview.panels`, e.g.
  `["doc","main","pdf"]`) maps to flex `order`; the ◀ ▶ `.mv` buttons
  in the doc/PDF pane headers move that pane among the three columns
  (any permutation reachable). Migrates the old `texreview.docside`
  setting.
- Live auto-refresh: a 3 s `pollState` interval refetches
  `/api/state`, compares a cheap signature (key/status/replies/edited/
  new), and re-renders only on change (toast for new suggestions).
  Ticks are skipped while hidden, a dialog is open, a reply is being
  edited, an input has focus, or a doc-pane selection is active.
- Reply threads are a full-width `.thread` section between diff/note
  and the action buttons; the selected card has an inline reply input
  (Enter posts to `/api/reply`). Human replies show a hover `✎` that
  swaps the text for an inline input (Enter saves, empty deletes, Esc
  cancels → `/api/reply_edit`). The toolbar `✎` input overrides the author name for
  replies/created suggestions (sessionStorage `texreview.user`;
  placeholder = server-side `getpass.getuser()`, sent via
  `GET /api/state` as `user`). Clicks inside `.rbox` are excluded from the card
  click handler so typing doesn't trigger a rerender. The `± N lines` toolbar input sets
  diff context (persisted in localStorage as `texreview.ctx`).
- Word-level diff = LCS over whitespace-preserving tokens (JS, capped;
  falls back to plain del/ins blocks on huge edits).
- Document pane (`⧉ Doc` / `d`, persisted as `texreview.doc`, hidden
  under 1100px): sticky side `<pre>` showing the selected suggestion's
  whole file verbatim with line numbers; all suggestion spans in that
  file are `<mark>`ed by status, selection syncs both ways (card ↔
  mark click), refetched when state reloads. Pane-header buttons:
  `＋ suggest` (enabled while text is selected in the pane: maps the
  selection to file offsets via a sentinel node, verifies against the
  cached text, then a dialog edits replacement/tags/comment and POSTs
  `/api/suggest`), `wrap`/`no-wrap` (horizontal scroll), and `⇄ side`
  (left/right), the latter two persisted (`texreview.wrap`,
  `texreview.docside`). An overview ruler (right strip, `#ruler`)
  shows one status-colored marker per suggestion plus a viewport
  thumb; click/drag seeks, clicking a marker selects that suggestion.
  The pane is a flex column (`#dochdr` / `#docmid` = scrolling
  `#docbody` + `#ruler`), so the ruler and header never scroll.
  Each line
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
pip install -e ".[test]"                     # flit_core build backend
python -m pytest                             # the real gate — keep green
tex-review check example/review              # anchors resolve
```

The pytest suite (`tests/`) covers matching, dedupe, overrides,
manual suggestions, replies, apply invariants, single-file mode, and
the HTTP API — extend it with any new behavior.
`tests/test_ui_browser.py` drives the real UI (incl. PDF.js
rendering) headlessly; it auto-skips unless playwright + chromium +
pdflatex are present (`pip install playwright && playwright install
chromium`). Browser-only behavior
(doc pane, ruler, dialogs) still needs a manual pass:

```
tex-review review example/review             # manual UI pass
cp -r example /tmp/ex && tex-review review /tmp/ex/review
# accept a few (incl. s002+s003: same line), apply, inspect paper.tex
```
