# PLAN

Roadmap for turning tex-review from a working script into an
installable, tested tool. Rough priority order; each phase is
independently shippable.

## Phase 1 — Test suite (before any restructuring)

Pytest covering the current behavior, so packaging/refactors can't
silently break invariants:

- Anchor matching: `ok`, `missing`, `ambiguous`, `occurrence`
  resolution, `bad-file`.
- Dedupe: identical suggestion in two passes → one item, first wins;
  decisions keyed by `content_key` survive pass re-emission.
- Overlap detection between *accepted* spans only.
- Apply: right-to-left ordering (two edits on one line), final
  `text[a:b] == old` race guard, `.bak` created once, skipped items
  reported and never applied, pass files byte-identical after apply.
- Both storage modes: directory (`decisions.json`) and legacy
  single-file write-back.
- Atomic writes (tmp + replace) — at least that no partial file is
  left on a simulated failure.

Constraint: pytest is a dev-only dependency; the tool itself stays
stdlib-only.

## Phase 2 — Packaging (installable tool) — DONE except publishing

Done: flit package (`pyproject.toml`, `texreview/` with `core.py` /
`server.py` / `cli.py`), console entry point `tex-review` with
subcommands `review` (alias `serve`), `apply`, `check`, and `instruct`
(prints the agent prompt from `texreview/instructions.md` plus
`texreview/schema.json`; `--schema` for schema only). `index.html`
loads via `importlib.resources`; local MathJax is looked up in
`./mathjax/` under the working directory; `--open` launches a browser;
`review.py` remains as a checkout shim.

Remaining:

- Check name availability on PyPI (`tex-review` / `texreview`), add a
  LICENSE, and publish (`flit publish`).
- CI: run pytest on 3.9–3.13, plus `tex-review check example/review`
  as a smoke test.

## Phase 3 — Review-flow features

- ~~**Inline editing of `new`** before accepting.~~ Done: `✎ Edit` /
  `e` edits `new`/`reasoning`/`tags` of any non-applied suggestion;
  stored as `{"status": ..., "new": ...}` dicts in `decisions.json`
  (bare-string form still read forever), in-place in single-file mode.
- **Revert** of an applied suggestion: inverse replace while `new`
  still matches uniquely; status back to `accepted` (or a new
  `reverted`).
- **Occurrence disambiguation UI**: for `ambiguous` anchors, show the N
  matches in context and let the reviewer click one (stores
  `occurrence` in the decision, since pass files are read-only).

## Phase 4 — Nice-to-haves (unordered)

- TeX syntax highlighting in the document pane. The pane is a plain
  `<pre>` by design (zero deps, verbatim bytes); we _could_ layer
  highlighting on top, either a small hand-rolled tokenizer
  (comments, `\commands`, math `$...$`, braces) or CodeMirror 5
  (single UMD file) loaded like MathJax: local copy first, CDN
  fallback, plain text if neither. CM6 is out — ESM module tree, no
  clean no-build story.

- `--base-dir` override on the CLI.
- Apply report persisted (e.g. `review/applied.log`) for audit.
- Dark mode / print-friendly stylesheet.
- Optional git integration: refuse to apply on a dirty manuscript
  unless `--force`, or auto-commit after apply.

## Non-goals

- No runtime dependencies, ever (stdlib-only is the product).
- No build step for the UI.
- No remote/multi-user server; localhost only.
