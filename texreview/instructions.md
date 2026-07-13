You are reviewing a LaTeX manuscript. **Do not edit the `.tex` files
directly.** Instead, write every proposed edit as a suggestion in a
single JSON file; a human will accept or reject each suggestion
individually with a separate tool, which then applies only the
accepted ones.

## Where to write

Create a **new** file in the manuscript's `review/` directory (create
the directory if it doesn't exist), named `NNNN-<short-name>.json`,
where `NNNN` is the next free 4-digit number (e.g.
`0001-grammar-pass.json`, `0003-notation-pass.json`). Never modify or
delete existing files in `review/` — each pass is append-only, and
`decisions.json`, `manual.json` and `comments.json` in that directory
belong to the human reviewer.

## File format

```json
{
  "version": 1,
  "base_dir": "..",
  "suggestions": [
    {
      "id": "s001",
      "file": "paper.tex",
      "old": "shows that the the results are",
      "new": "shows that the results are",
      "occurrence": 1,
      "reasoning": "Duplicated word.",
      "tags": ["typo"]
    }
  ]
}
```

Optionally set `author` (your name/model, self-declared) and `date`
(ISO 8601) on each suggestion. To respond to a suggestion from an
earlier pass — e.g. to answer a reviewer's comment — add a top-level
`"replies"` array to your (new) pass file instead of re-emitting it:

```json
"replies": [
  {"to": "s001", "text": "Agreed, but note the theorem numbering.",
   "author": "agent-pass-3", "date": "2026-07-13T09:00:00Z"}
]
```

`to` is the target suggestion's id (or its content key as reported by
the tool). Replies never modify the suggestion itself.

Fields per suggestion:

| field        | required | meaning                                                        |
|--------------|----------|----------------------------------------------------------------|
| `file`       | yes      | path relative to `base_dir` (with `base_dir: ".."`, relative to the manuscript root that contains `review/`) |
| `old`        | yes      | the exact text to replace, copied **verbatim** from the file — every character, including whitespace, line breaks, `%` comments, and LaTeX markup |
| `new`        | yes      | the replacement text; `""` deletes the anchored text            |
| `occurrence` | no       | 1-based index, required if `old` appears more than once in the file |
| `reasoning`  | no       | one sentence explaining the change; shown to the reviewer, may contain math as `$...$` |
| `tags`       | no       | short labels such as `grammar`, `clarity`, `typo`, `notation`  |
| `id`         | no       | short unique id within the file, e.g. `s001`, `s002`, …        |

## Rules for good suggestions

1. **One logical change per suggestion.** Do not bundle a typo fix and
   a rewording into one item; the reviewer decides each independently.
2. **`old` must match exactly.** Copy it from the file, never retype
   it from memory. If it does not match byte-for-byte, the suggestion
   cannot be applied.
3. **`old` must be unique in the file** — extend it with surrounding
   words until it is, or set `occurrence`. Keep it as short as
   uniqueness allows (a phrase, not a paragraph).
4. **Suggestions must not overlap.** No two suggestions (in this or
   any previous pass) may touch the same span of text. If two changes
   affect the same words, merge them into one suggestion.
5. **Preserve LaTeX validity.** `new` must compile in place of `old`:
   keep braces balanced, don't break environments or math delimiters.
6. **Give a one-sentence `reasoning` and at least one tag** for every
   suggestion.
7. **Don't re-suggest** something already present in an earlier pass
   file, and don't suggest edits to text you yourself proposed in
   `new` — suggestions anchor to the file as it is on disk now.

## Self-check before finishing

For every suggestion, verify programmatically that `old` occurs in the
named file exactly once (or exactly `occurrence` and at least that many
times), e.g. with a short script that counts
`file_text.count(old)`. Fix any anchor that is missing or ambiguous.
If the `tex-review` tool is available, `tex-review check review/` does
this check for you — every line must report `ok`.
