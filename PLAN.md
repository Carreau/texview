# PLAN

Roadmap for turning tex-review from a working script into an
installable, tested tool. Rough priority order; each phase is
independently shippable.

## Phase 1 — Test suite — DONE

`tests/` (pytest, dev-only extra `pip install -e ".[test]"`) covers
everything below; 60+ tests. Keep it green and extend with new
behavior.

## Phase 1 (original scope, all covered)

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

Also done since: MIT `LICENSE`, version 0.2.0, `tex-review` confirmed
free on PyPI, `.github/workflows/ci.yml` (pytest on 3.9–3.13 + check
smoke test) and `publish.yml` (trusted publishing on `v*` tags).

Remaining (manual, one-time): register the trusted publisher on
pypi.org (project `tex-review` -> Publishing -> this repo,
`publish.yml`, environment `pypi`), then `git tag v0.2.0 && git push
--tags`.

## Phase 3 — Review-flow features

- ~~**Revert** of an applied suggestion.~~ Done: `applied_at`
  recorded at apply; `Store.revert` / `POST /api/revert` / `↶ Revert`
  on applied cards; status returns to pending.
- ~~**Inline editing of `new`** before accepting.~~ Done: `✎ Edit` /
  `e` edits `new`/`reasoning`/`tags` of any non-applied suggestion;
  stored as `{"status": ..., "new": ...}` dicts in `decisions.json`
  (bare-string form still read forever), in-place in single-file mode.
- ~~**Purge**~~ Done: `tex-review purge` (`--dry-run`, `--status`)
  rewrites pass files without resolved items, deletes emptied ones,
  prunes decisions/comments.
- ~~**Static hosting mode**~~ Done: local mode in index.html (auto-
  detected; 📂 Open / drag-drop, in-browser engine, ⬇ Save downloads),
  Pages deploy workflow, node-vs-Python parity tests.
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
