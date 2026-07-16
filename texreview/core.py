"""Suggestion store, anchor matching, and apply engine.

Two layouts are supported:

  Directory mode (recommended): point at a directory of *.json files.
  Each file is one agent pass and is never modified by this tool; your
  accept/reject decisions live in <dir>/decisions.json, keyed by a
  content hash so they survive agents re-emitting the same suggestion.
  Files are re-scanned on every UI refresh, so an agent can keep adding
  passes while the server runs.

  Single-file mode: point at one suggestions.json; statuses are written
  back into it (original behavior).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

DECISIONS = "decisions.json"
MANUAL = "manual.json"     # tool-owned pass file for human-created items
COMMENTS = "comments.json"  # tool-owned: content_key -> [reply, ...]
VALID_STATUSES = {"pending", "accepted", "rejected", "applied"}
EDITABLE = ("new", "reasoning", "tags")   # human-editable fields


def content_key(s: dict) -> str:
    blob = json.dumps(
        [s.get("file"), s.get("old"), s.get("new"), s.get("occurrence")],
        ensure_ascii=False,
    )
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- store ----

class Store:
    """Owns the suggestion set and the manuscript files it points at.

    Directory mode: suggestions come from <dir>/*.json (agent-owned,
    read-only here); statuses persist in <dir>/decisions.json.
    Single-file mode: statuses are written back into the file itself.
    """

    def __init__(self, path: Path):
        self.path = path.resolve()
        self.dir_mode = self.path.is_dir()
        self.lock = threading.Lock()
        self.suggestions: list[dict] = []
        self.single_doc: dict = {}
        self.overrides: dict[str, dict] = {}   # key -> human edits (dir)
        self.reload()

    # -- loading ------------------------------------------------------

    def reload(self) -> None:
        if self.dir_mode:
            self._load_dir()
        else:
            self._load_single()

    def _normalize(self, items, default_id, base: Path, source: str,
                   statuses: dict | None) -> None:
        for i, s in enumerate(items):
            s = dict(s)   # don't pollute the caller's dicts (they may be
            #               written back verbatim in single-file mode)
            s["key"] = content_key(s)
            s.setdefault("id", f"{default_id}#{i + 1}")
            s["source"] = source
            s["_base"] = base
            if statuses is not None:
                s["status"] = statuses.get(s["key"], "pending")
            else:
                s.setdefault("status", "pending")
            self.suggestions.append(s)

    def _load_dir(self) -> None:
        dpath = self.path / DECISIONS
        # decisions.json values: either a bare status string, or
        # {"status": ..., "new"/"reasoning"/"tags": ...} when the human
        # edited the suggestion (pass files stay read-only)
        statuses, self.overrides = {}, {}
        if dpath.is_file():
            for k, v in json.loads(
                    dpath.read_text(encoding="utf-8")).items():
                if isinstance(v, dict):
                    statuses[k] = v.get("status", "pending")
                    ov = {f: v[f] for f in EDITABLE + ("applied_at",)
                          if f in v}
                    if ov:
                        self.overrides[k] = ov
                else:
                    statuses[k] = v
        self.suggestions = []
        pass_replies = []          # (to, reply) from pass files' "replies"
        for f in sorted(self.path.glob("*.json")):
            if f.name in (DECISIONS, COMMENTS) or f.name.endswith(".tmp"):
                continue
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                print(f"warning: skipping {f.name}: {e}", file=sys.stderr)
                continue
            if isinstance(raw, list):
                items, rel = raw, ".."
            else:
                items = raw.get("suggestions", [])
                rel = raw.get("base_dir", "..")
                for rep in raw.get("replies", []):
                    if isinstance(rep, dict) and rep.get("to") \
                            and rep.get("text"):
                        pass_replies.append((rep["to"], {
                            k: rep[k] for k in
                            ("text", "author", "date") if k in rep}))
            base = (f.parent / rel).resolve()
            self._normalize(items, f.stem, base, f.name, statuses)
        # dedupe re-emitted suggestions (same content key): keep first
        seen: set[str] = set()
        uniq, ids = [], set()
        for s in self.suggestions:
            if s["key"] in seen:
                continue
            seen.add(s["key"])
            while s["id"] in ids:           # keep ids unique for the UI
                s["id"] += "'"
            ids.add(s["id"])
            uniq.append(s)
        self.suggestions = uniq
        for s in self.suggestions:      # human edits win over pass content
            ov = self.overrides.get(s["key"])
            if ov:
                s.update(ov)
                if any(f in ov for f in EDITABLE):
                    s["edited"] = True
        # thread replies: pass-file "replies" (to = id or content key)
        # + tool-owned comments.json (keyed by content key)
        cpath = self.path / COMMENTS
        comments = {}
        if cpath.is_file():
            comments = json.loads(cpath.read_text(encoding="utf-8"))
        id_to_key = {s["id"]: s["key"] for s in self.suggestions}
        keys = set(id_to_key.values())
        merged: dict = {}
        for to, rep in pass_replies:
            k = to if to in keys else id_to_key.get(to)
            if k:
                merged.setdefault(k, []).append(rep)
        for k, reps in comments.items():
            # human replies are editable; ci = index in comments.json
            merged.setdefault(k, []).extend(
                {**r, "editable": True, "ci": i}
                for i, r in enumerate(reps))
        for s in self.suggestions:
            reps = list(s.get("replies", [])) + merged.get(s["key"], [])
            if reps:
                s["replies"] = sorted(reps, key=lambda r: r.get("date", ""))

    def _load_single(self) -> None:
        self.single_doc = json.loads(self.path.read_text(encoding="utf-8"))
        base = (self.path.parent
                / self.single_doc.get("base_dir", ".")).resolve()
        self.suggestions = []
        self._normalize(self.single_doc.get("suggestions", []),
                        "s", base, self.path.name, statuses=None)
        for s in self.suggestions:      # inline replies are editable
            if s.get("replies"):
                s["replies"] = [{**r, "editable": True, "ci": i}
                                for i, r in enumerate(s["replies"])]

    # -- saving -------------------------------------------------------

    @staticmethod
    def _write(path: Path, obj) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(obj, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)

    def save(self) -> None:
        if self.dir_mode:
            decided = {}
            for s in self.suggestions:
                ov = self.overrides.get(s["key"])
                if ov:
                    decided[s["key"]] = {"status": s["status"], **ov}
                elif s["status"] != "pending":
                    decided[s["key"]] = s["status"]
            self._write(self.path / DECISIONS, decided)
        else:
            for s in self.single_doc.get("suggestions", []):
                live = self.suggestion_by_key(content_key(s))
                if live:
                    s["status"] = live["status"]
                    if "applied_at" in live:
                        s["applied_at"] = live["applied_at"]
                    else:
                        s.pop("applied_at", None)
            self._write(self.path, self.single_doc)

    # -- manual suggestions (human-created, via the UI) ---------------

    def manual_base(self) -> Path:
        """base_dir a manual suggestion's `file` is relative to."""
        if self.dir_mode:
            return (self.path / "..").resolve()
        return (self.path.parent
                / self.single_doc.get("base_dir", ".")).resolve()

    def add_manual(self, s: dict) -> dict:
        """Append a human-created suggestion and reload.

        Directory mode: goes into <dir>/manual.json (tool-owned; agent
        pass files stay untouched). Single-file mode: appended to the
        file itself. Returns the live (deduped) suggestion.
        """
        if self.dir_mode:
            p = self.path / MANUAL
            doc = {"version": 1, "base_dir": "..", "suggestions": []}
            if p.is_file():
                raw = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    doc["suggestions"] = raw
                else:
                    doc = raw
                    doc.setdefault("suggestions", [])
            items, target, payload = doc["suggestions"], p, doc
        else:
            items = self.single_doc.setdefault("suggestions", [])
            target, payload = self.path, self.single_doc
        taken = {x.get("id") for x in items} | {
            x["id"] for x in self.suggestions}
        n = 1
        while f"m{n:03d}" in taken:
            n += 1
        s = {**s, "id": f"m{n:03d}"}
        items.append(s)
        self._write(target, payload)
        self.reload()
        return self.suggestion_by_key(content_key(s)) or s

    def edit(self, sid: str, fields: dict) -> dict | None:
        """Human-edit a suggestion's `new`/`reasoning`/`tags`.

        Directory mode: stored as an override in decisions.json, keyed
        by the suggestion's original content key — pass files stay
        read-only and edits survive re-emission. Single-file mode:
        written into the file itself. `old`/`occurrence` are identity
        and cannot change.
        """
        s = self.suggestion(sid)
        if s is None:
            return None
        fields = {f: fields[f] for f in EDITABLE if f in fields}
        if self.dir_mode:
            self.overrides.setdefault(s["key"], {}).update(fields)
            s.update(fields)
            s["edited"] = True
            self.save()
        else:
            for raw in self.single_doc.get("suggestions", []):
                if content_key(raw) == s["key"]:
                    raw.update(fields)
                    break
            self._write(self.path, self.single_doc)
            self.reload()
        return self.suggestion(sid)

    def add_reply(self, sid: str, reply: dict) -> dict | None:
        """Attach a reply to a suggestion (comments.json / the single
        file). `reply` = {text, author, date}; authorship is trusted,
        not authenticated."""
        s = self.suggestion(sid)
        if s is None:
            return None
        if self.dir_mode:
            cpath = self.path / COMMENTS
            comments = {}
            if cpath.is_file():
                comments = json.loads(cpath.read_text(encoding="utf-8"))
            comments.setdefault(s["key"], []).append(reply)
            self._write(cpath, comments)
        else:
            for raw in self.single_doc.get("suggestions", []):
                if content_key(raw) == s["key"]:
                    raw.setdefault("replies", []).append(reply)
                    break
            self._write(self.path, self.single_doc)
        self.reload()
        return self.suggestion(sid)

    def purge(self, statuses=("applied", "rejected"),
              dry_run: bool = False) -> dict:
        """Remove resolved suggestions from disk to cut churn/repo size.

        The ONE deliberate exception to "pass files are read-only":
        explicitly human-invoked (CLI `tex-review purge`), it rewrites
        pass files without the purged items, deletes files left empty,
        and prunes decisions.json / comments.json of the dead keys.
        Replies targeting a purged suggestion are dropped as well.
        Purged applied items lose `applied_at` (no revert afterwards).
        """
        statuses = set(statuses)
        doomed_keys = {s["key"] for s in self.suggestions
                       if s["status"] in statuses}
        doomed_ids = {s["id"] for s in self.suggestions
                      if s["key"] in doomed_keys}
        report = {"removed": sorted(doomed_ids),
                  "rewritten": [], "deleted": []}
        if not doomed_keys or dry_run:
            return report
        doomed = doomed_keys | doomed_ids
        if self.dir_mode:
            for f in sorted(self.path.glob("*.json")):
                if f.name in (DECISIONS, COMMENTS) \
                        or f.name.endswith(".tmp"):
                    continue
                try:
                    raw = json.loads(f.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                is_list = isinstance(raw, list)
                items = raw if is_list else raw.get("suggestions", [])
                kept = [s for s in items
                        if content_key(s) not in doomed_keys]
                changed = len(kept) != len(items)
                reps = None if is_list else raw.get("replies")
                if reps is not None:
                    reps_kept = [r for r in reps
                                 if r.get("to") not in doomed]
                    changed |= len(reps_kept) != len(reps)
                    reps = reps_kept
                if not changed:
                    continue
                if not kept and not reps:
                    f.unlink()
                    report["deleted"].append(f.name)
                    continue
                if is_list:
                    self._write(f, kept)
                else:
                    raw["suggestions"] = kept
                    if "replies" in raw:
                        if reps:
                            raw["replies"] = reps
                        else:
                            del raw["replies"]
                    self._write(f, raw)
                report["rewritten"].append(f.name)
            for name in (DECISIONS, COMMENTS):
                p = self.path / name
                if p.is_file():
                    d = json.loads(p.read_text(encoding="utf-8"))
                    d2 = {k: v for k, v in d.items()
                          if k not in doomed_keys}
                    if d2 != d:
                        self._write(p, d2)
        else:
            items = self.single_doc.get("suggestions", [])
            self.single_doc["suggestions"] = [
                s for s in items if content_key(s) not in doomed_keys]
            self._write(self.path, self.single_doc)
        self.reload()
        return report

    def revert(self, sid: str):
        """Undo an applied suggestion: inverse replace (`new` -> `old`)
        while the applied text can still be located. Status returns to
        pending. Returns True, an error-reason string, or None if the
        suggestion is unknown or not applied."""
        s = self.suggestion(sid)
        if s is None or s["status"] != "applied":
            return None
        old, new = s.get("old", ""), s.get("new", "")
        if not new:
            return "deleted-text"    # nothing left to anchor the undo on
        text = self.file_text(s)
        if text is None:
            return "bad-file"
        at = s.get("applied_at")
        if isinstance(at, int) and text[at:at + len(new)] == new:
            pos = at
        else:
            hits = find_occurrences(text, new)
            if not hits:
                return "missing"
            if len(hits) > 1:
                return "ambiguous"
            pos = hits[0]
        path = (s["_base"] / s.get("file", "")).resolve()
        path.write_text(text[:pos] + old + text[pos + len(new):],
                        encoding="utf-8")
        s["status"] = "pending"
        s.pop("applied_at", None)
        if self.dir_mode:
            ov = self.overrides.get(s["key"])
            if ov:
                ov.pop("applied_at", None)
                if not ov:
                    del self.overrides[s["key"]]
        self.save()
        self.reload()
        return True

    def edit_reply(self, sid: str, ci: int, text: str):
        """Edit (or, with empty text, delete) a human reply. `ci` is
        the reply's index in comments.json / the item's inline list.
        Agent replies (from pass files) are not editable."""
        s = self.suggestion(sid)
        if s is None:
            return None
        if self.dir_mode:
            cpath = self.path / COMMENTS
            comments = {}
            if cpath.is_file():
                comments = json.loads(cpath.read_text(encoding="utf-8"))
            lst = comments.get(s["key"], [])
            if not 0 <= ci < len(lst):
                return None
            if text:
                lst[ci]["text"] = text
            else:
                del lst[ci]
                if not lst:
                    comments.pop(s["key"], None)
            self._write(cpath, comments)
        else:
            for raw in self.single_doc.get("suggestions", []):
                if content_key(raw) == s["key"]:
                    lst = raw.get("replies", [])
                    if not 0 <= ci < len(lst):
                        return None
                    if text:
                        lst[ci]["text"] = text
                    else:
                        del lst[ci]
                        if not lst:
                            raw.pop("replies", None)
                    break
            else:
                return None
            self._write(self.path, self.single_doc)
        self.reload()
        return True

    # -- lookups ------------------------------------------------------

    def file_text(self, s: dict) -> str | None:
        base: Path = s["_base"]
        p = (base / s.get("file", "")).resolve()
        if not str(p).startswith(str(base)) or not p.is_file():
            return None
        return p.read_text(encoding="utf-8")

    def suggestion(self, sid: str) -> dict | None:
        return next((s for s in self.suggestions if s["id"] == sid), None)

    def suggestion_by_key(self, key: str) -> dict | None:
        return next((s for s in self.suggestions if s["key"] == key), None)


# ------------------------------------------------------------- matching ----

@dataclass
class Match:
    state: str            # ok | missing | ambiguous | overlap | bad-file
    start: int = -1
    end: int = -1
    count: int = 0
    line: int = 0
    context: str = ""     # a few surrounding lines
    ctx_start: int = 0    # offset of `old` within context


def find_occurrences(text: str, needle: str) -> list[int]:
    out, i = [], text.find(needle)
    while i != -1:
        out.append(i)
        i = text.find(needle, i + 1)
    return out


def locate(text: str | None, s: dict, ctx: int = 1) -> Match:
    if text is None:
        return Match("bad-file")
    old = s.get("old", "")
    if not old:
        return Match("missing")
    hits = find_occurrences(text, old)
    if not hits:
        return Match("missing", count=0)
    occ = s.get("occurrence")
    if occ is None:
        if len(hits) > 1:
            return Match("ambiguous", count=len(hits))
        occ = 1
    if not (1 <= occ <= len(hits)):
        return Match("missing", count=len(hits))
    start = hits[occ - 1]
    end = start + len(old)
    line = text.count("\n", 0, start) + 1
    # context: the anchor's own line(s) plus `ctx` full lines either side
    ctx_a = text.rfind("\n", 0, start) + 1
    for _ in range(max(0, ctx)):
        if ctx_a == 0:
            break
        ctx_a = text.rfind("\n", 0, ctx_a - 1) + 1
    ctx_b = text.find("\n", end)
    for _ in range(max(0, ctx)):
        if ctx_b == -1:
            break
        ctx_b = text.find("\n", ctx_b + 1)
    ctx_b = len(text) if ctx_b == -1 else ctx_b
    return Match(
        "ok", start, end, len(hits), line,
        context=text[ctx_a:ctx_b], ctx_start=start - ctx_a,
    )


def annotate(store: Store, ctx: int = 1) -> list[dict]:
    """Suggestions + live match info, with overlap detection per file."""
    texts: dict[tuple, str | None] = {}
    out = []
    spans: dict[tuple, list[tuple[int, int, str]]] = {}
    for s in store.suggestions:
        fkey = (str(s["_base"]), s.get("file", ""))
        if fkey not in texts:
            texts[fkey] = store.file_text(s)
        m = locate(texts[fkey], s, ctx)
        if m.state == "ok" and s["status"] != "applied":
            spans.setdefault(fkey, []).append((m.start, m.end, s["id"]))
        row = {k: v for k, v in s.items() if k != "_base"}
        row["fkey"] = "|".join(fkey)   # opaque per-file key for the UI
        row["match"] = m.__dict__
        out.append(row)
    # overlap: only matters between two *accepted* suggestions
    accepted = {s["id"] for s in store.suggestions
                if s["status"] == "accepted"}
    for fkey, sp in spans.items():
        sp.sort()
        for (a1, b1, id1), (a2, b2, id2) in zip(sp, sp[1:]):
            if a2 < b1 and id1 in accepted and id2 in accepted:
                for row in out:
                    if row["id"] in (id1, id2):
                        row["match"]["state"] = "overlap"
    return out


# ---------------------------------------------------------------- apply ----

def apply_accepted(store: Store) -> dict:
    """Apply accepted+ok suggestions, back files up once, mark applied."""
    rows = annotate(store)
    todo = [r for r in rows if r["status"] == "accepted"]
    ok = [r for r in todo if r["match"]["state"] == "ok"]
    skipped = [
        {"id": r["id"], "reason": r["match"]["state"]}
        for r in todo if r["match"]["state"] != "ok"
    ]
    by_file: dict[Path, list[dict]] = {}
    for r in ok:
        s = store.suggestion(r["id"])
        path = (s["_base"] / r["file"]).resolve()
        by_file.setdefault(path, []).append(r)

    applied = []
    for path, group in by_file.items():
        text = path.read_text(encoding="utf-8")
        bak = path.with_name(path.name + ".bak")
        if not bak.exists():
            shutil.copy2(path, bak)
        done = []                              # (row, original offset)
        # right-to-left so earlier offsets stay valid
        for r in sorted(group, key=lambda r: -r["match"]["start"]):
            a, b = r["match"]["start"], r["match"]["end"]
            if text[a:b] != r["old"]:          # paranoia
                skipped.append({"id": r["id"], "reason": "shifted"})
                continue
            text = text[:a] + r.get("new", "") + text[b:]
            applied.append(r["id"])
            done.append((r, a))
        path.write_text(text, encoding="utf-8")
        # where each replacement sits in the *final* text (for revert):
        # its own offset shifted by the length deltas of edits before it
        for r, a in done:
            delta = sum(len(x.get("new", "")) - len(x["old"])
                        for x, xa in done if xa < a)
            s = store.suggestion(r["id"])
            s["applied_at"] = a + delta
            if store.dir_mode:
                store.overrides.setdefault(
                    s["key"], {})["applied_at"] = a + delta

    for sid in applied:
        store.suggestion(sid)["status"] = "applied"
    store.save()
    return {"applied": applied, "skipped": skipped}
