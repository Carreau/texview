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
VALID_STATUSES = {"pending", "accepted", "rejected", "applied"}


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
        statuses = {}
        if dpath.is_file():
            statuses = json.loads(dpath.read_text(encoding="utf-8"))
        self.suggestions = []
        for f in sorted(self.path.glob("*.json")):
            if f.name == DECISIONS or f.name.endswith(".tmp"):
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

    def _load_single(self) -> None:
        self.single_doc = json.loads(self.path.read_text(encoding="utf-8"))
        base = (self.path.parent
                / self.single_doc.get("base_dir", ".")).resolve()
        self.suggestions = []
        self._normalize(self.single_doc.get("suggestions", []),
                        "s", base, self.path.name, statuses=None)

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
            decided = {s["key"]: s["status"] for s in self.suggestions
                       if s["status"] != "pending"}
            self._write(self.path / DECISIONS, decided)
        else:
            for s in self.single_doc.get("suggestions", []):
                live = self.suggestion_by_key(content_key(s))
                if live:
                    s["status"] = live["status"]
            self._write(self.path, self.single_doc)

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
        # right-to-left so earlier offsets stay valid
        for r in sorted(group, key=lambda r: -r["match"]["start"]):
            a, b = r["match"]["start"], r["match"]["end"]
            if text[a:b] != r["old"]:          # paranoia
                skipped.append({"id": r["id"], "reason": "shifted"})
                continue
            text = text[:a] + r.get("new", "") + text[b:]
            applied.append(r["id"])
        path.write_text(text, encoding="utf-8")

    for sid in applied:
        store.suggestion(sid)["status"] = "applied"
    store.save()
    return {"applied": applied, "skipped": skipped}
