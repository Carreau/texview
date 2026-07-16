# tex-review

Review agent-suggested edits to a manuscript one change at a time, then
apply only the accepted ones. Changes are anchored on exact text, not on
line numbers or git hunks, so several independent edits on the same line
never collide.

Stdlib-only Python (3.9+), no runtime dependencies, runs entirely
locally.

## Install

```
pipx install tex-review        # or: uvx tex-review ...
# from a checkout:
pip install .
```

This provides the `tex-review` command (`python -m texreview` works
too, and `python review.py ...` still works from a checkout).

## Layout (recommended: a review/ directory)

```
manuscript/
  paper.tex
  review/
    0001-grammar-pass.json    <- written by the agent, never modified here
    0002-notation-pass.json   <- another pass; drop as many as you like
    decisions.json            <- your accept/reject decisions (tool-owned)
```

The split matters: the agent only ever *creates new files* in `review/`
(no read-modify-write of a shared JSON, so no clobbering and passes can
run in parallel), while your decisions live in `decisions.json`, keyed
by a content hash of `(file, old, new, occurrence)`. Decisions therefore
survive an agent re-emitting the same suggestion, and identical
suggestions across passes are deduped automatically. Deleting a pass is
just `rm review/0002-*.json`.

Pointing the tool at a single `suggestions.json` file still works
(statuses are then written back into that file).

## Workflow

1. The agent writes each pass as a new file in `review/` (schema below).
2. Review in the browser:

   ```
   tex-review review manuscript/review
   # → http://127.0.0.1:8123   (--port N, --open to launch a browser)
   ```

   Keyboard: `j`/`k` move, `a` accept, `r` reject, `u` back to pending,
   `+`/`-` more/less context around each change (also a `± N lines`
   toolbar input; remembered by the browser), `d` a side pane showing
   the whole document with every suggestion highlighted (click a
   highlight to jump to its card), `A` apply, `?` help. In the
   document pane you can also select any text and hit **＋ suggest**
   to write a suggestion of your own (replacement, tags, comment); it
   lands in `review/manual.json` and is reviewed/applied like any
   other. Accept/reject auto-advances to the next pending item. The
   directory is re-scanned on every refresh, so an agent can keep adding
   passes while you review.
3. Click **Apply accepted** (or `tex-review apply ...`). Each
   touched file gets a one-time `<name>.bak` backup; accepted changes
   are applied right-to-left within each file so offsets never shift,
   and each suggestion is marked `applied` in `decisions.json`.

`tex-review check manuscript/review` validates all anchors from the
command line (useful in the agent loop or CI).

## Pass-file schema

```json
{
  "version": 1,
  "base_dir": "..",
  "suggestions": [
    {
      "id": "s001",
      "file": "paper.tex",
      "old": "the the results",
      "new": "the results",
      "occurrence": 1,
      "reasoning": "Duplicated word.",
      "tags": ["typo"]
    }
  ]
}
```

A bare JSON list of suggestion objects is also accepted.

| field        | required | meaning                                     |
|--------------|----------|---------------------------------------------|
| `file`       | yes      | path relative to `base_dir`                 |
| `old`        | yes      | exact text to replace, verbatim (whitespace included) |
| `new`        | yes      | replacement text (empty string deletes)     |
| `occurrence` | no       | 1-based index if `old` appears more than once |
| `reasoning`  | no       | shown as a margin note in the UI            |
| `tags`       | no       | e.g. `["grammar"]` — shown as chips         |
| `id`         | no       | auto-filled from filename if missing        |
| `author`, `date` | no   | who proposed it (self-declared) and when (ISO 8601) |

A pass file may also carry a top-level `"replies"` array —
`{"to": "<id or content key>", "text": "...", "author": "...",
"date": "..."}` — to respond to suggestions from earlier passes.
Replies you write in the UI land in `review/comments.json` with your
username and a timestamp (trusted, not authenticated); threads show
under each suggestion's margin note, sorted by date.
| `base_dir`   | no       | relative to the pass file; defaults to `..` (i.e. `review/` sits inside the manuscript root) |

## Instructions to give your agent

`tex-review instruct` prints a self-contained prompt (schema, rules,
self-check) to paste into an agent that cannot see this repo;
`tex-review instruct --schema` prints just the machine-readable JSON
Schema. The same text lives in
[texreview/instructions.md](texreview/instructions.md). Short version:

> Do not edit the `.tex` files. Instead, write your proposed edits as a
> NEW file `review/NNNN-<short-name>.json` (next free number) following
> the schema of the existing files; never modify existing files in
> `review/`. One logical change per suggestion. `old` must be copied
> verbatim from the file and should be long enough to be unique — extend
> it with surrounding words if needed, or set `occurrence`. Keep
> suggestions independent: two suggestions must not overlap in the text
> they touch. Give a one-sentence `reasoning` for each, and a short tag
> such as grammar, clarity, typo, or notation. Run
> `tex-review check review/` and fix any anchor it flags before
> finishing.

## Anchor states you may see in the UI

- **ok** — anchor found exactly once (or `occurrence` resolves it).
- **not found** — the file changed (or the agent mistyped `old`); the
  suggestion is skipped on apply.
- **ambiguous** — `old` matches several places and no `occurrence` set.
- **overlap** — two *accepted* suggestions touch the same span; accept
  only one, or reword them.

Skipped suggestions are never silently applied — the apply report lists
them, and they stay `accepted` so you can fix and re-apply.

## PDF panel (SyncTeX)

If a compiled `paper.pdf` sits next to `paper.tex`, the **📄 PDF**
toggle (or `P`) opens a third panel showing the PDF, and it follows
your selection: each suggestion is forward-searched with SyncTeX and
the viewer jumps to the right page. Requirements: compile with
`-synctex=1` (so `paper.synctex.gz` exists) and have the `synctex`
CLI on the PATH (ships with TeX Live/MacTeX).

Prefer your own PDF reader? Start the server with a forward-search
command template and the selection will drive it instead of the
in-page panel (placeholders: `{line}`, `{tex}`, `{pdf}`):

```
tex-review review review/ --pdf-viewer "zathura --synctex-forward {line}:1:{tex} {pdf}"
tex-review review review/ --pdf-viewer "okular --unique {pdf}#src:{line} {tex}"
tex-review review review/ --pdf-viewer "displayline {line} {pdf} {tex}"   # Skim (macOS)
```

The in-page panel uses the browser's built-in PDF viewer at page
granularity; external viewers give smooth, exact positioning. PDF
sync needs the server (it is not available in static/local mode).

## No-install / static mode

The UI is a single self-contained page. Served without the Python
backend it switches to **local mode**: files are loaded into the
browser, reviewed and applied there (nothing is uploaded anywhere),
and downloaded back when you're done.

### Publishing it

- **GitHub Pages (included):** the repo ships
  `.github/workflows/pages.yml`, which publishes the page on every
  push to `main`. One-time setup: in the repo's **Settings → Pages**,
  set *Source* to **GitHub Actions**. The reviewer then lives at
  `https://<user>.github.io/<repo>/`.
- **Any static host:** it is one file with zero assets — copy
  `texreview/static/index.html` to Netlify, S3, your web space, a
  `python -m http.server` directory, anywhere.
- **No host at all:** just double-click `index.html` (a `file://`
  URL works) or keep a copy next to your manuscript.

### Using it

1. Open the page. It shows "local mode — files stay in this browser"
   and 📂 Open / ⬇ Save buttons appear in the toolbar.
2. **Drag and drop** your `.tex` file(s) and the `review/*.json`
   passes anywhere onto the page (or pick them via 📂 Open). Also
   drop `decisions.json` / `comments.json` / `manual.json` if you are
   resuming an earlier session.
3. Review as usual — accept/reject, edit, reply, add your own
   suggestions from the document pane, **Apply accepted**, purge.
4. Hit **⬇ Save**: the browser downloads the edited `.tex` files plus
   `decisions.json` / `manual.json` / `comments.json` (and any pass
   files rewritten by purge). Move them back over the originals —
   they are byte-compatible with the CLI, so you can continue in
   either mode.

Everything stays on your machine; the only network request the page
ever makes is the optional MathJax CDN load.

## Housekeeping

Once suggestions are applied or rejected they only add noise and repo
size. `tex-review purge review/` (or the **🧹 Purge** button in the
UI) removes them from the pass files
(deleting files left empty) and prunes the matching entries from
`decisions.json`/`comments.json`; pending and accepted suggestions,
and replies to them, are untouched. Preview with `--dry-run`, choose
what to drop with `--status rejected`. This is the one command that
rewrites pass files — commit first.

## Git tip

Commit the manuscript before applying; then `review/` passes,
`decisions.json`, and the resulting diff each tell their own story.
Add `*.bak` to `.gitignore`.

## Math rendering

Margin notes (`reasoning`) and a rendered before/after preview of each
edit are typeset with MathJax (`$...$`, `\(...\)`, `$$...$$`), so the
agent can write math in its explanations. The **Σ TeX** toggle (or `m`)
turns previews on/off. The diff block itself always shows verbatim
source — that is the exact text that gets applied, so it is never
typeset.

MathJax loads from the jsDelivr CDN by default. For fully offline use,
drop a local copy in a `mathjax/` directory under the working directory
you run `tex-review` from, and it is picked up automatically:

```
curl --create-dirs -o mathjax/es5/tex-svg.js \
  https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js
```

If neither is reachable, everything still works — math just stays as
source text.
