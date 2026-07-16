"""Localhost HTTP server for the review UI."""

from __future__ import annotations

import getpass
import json
import mimetypes
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .core import (VALID_STATUSES, Store, annotate, apply_accepted,
                   content_key, locate)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def synctex_page(tex: Path, line: int, pdf: Path):
    """Forward search via the synctex CLI: source line -> PDF page.
    Returns None if synctex is missing, times out, or finds nothing
    (e.g. the PDF was not compiled with -synctex=1)."""
    exe = shutil.which("synctex")
    if exe is None:
        return None
    try:
        out = subprocess.run(
            [exe, "view", "-i", f"{max(1, line)}:1:{tex}",
             "-o", str(pdf)],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for ln in out.stdout.splitlines():
        if ln.startswith("Page:"):
            try:
                return int(ln.split(":", 1)[1])
            except ValueError:
                return None
    return None


def tilde(path: Path) -> str:
    """Abbreviate the home directory to ~ (display only)."""
    home = Path.home()
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


class Handler(BaseHTTPRequestHandler):
    store: Store  # set on the class before serving
    pdf_viewer = None  # forward-search command template (--pdf-viewer)

    def _send(self, code: int, body: bytes, ctype: str,
              cache: str = "no-store") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _pdf_for(self, s):
        """(abs tex path, abs pdf path or None) for a suggestion."""
        base = s["_base"]
        tex = (base / s.get("file", "")).resolve()
        pdf = tex.with_suffix(".pdf")
        if str(pdf).startswith(str(base)) and pdf.is_file():
            return tex, pdf
        return tex, None

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            page = files("texreview").joinpath(
                "static/index.html").read_bytes()
            self._send(200, page, "text/html; charset=utf-8")
        elif self.path.startswith("/mathjax/"):
            # optional local MathJax: ./mathjax/ in the working directory
            root = (Path.cwd() / "mathjax").resolve()
            rel = self.path[len("/mathjax/"):].split("?", 1)[0]
            p = (root / rel).resolve()
            if str(p).startswith(str(root)) and p.is_file():
                ctype = (mimetypes.guess_type(p.name)[0]
                         or "application/octet-stream")
                self._send(200, p.read_bytes(), ctype)
            else:  # no local copy: index.html falls back to the CDN
                self._send(404, b"not found", "text/plain")
        elif urlparse(self.path).path == "/api/file":
            # full text of the file a suggestion points at; resolved via
            # the store (same containment guard as file_text), so no
            # client-supplied paths
            sid = parse_qs(urlparse(self.path).query).get("id", [""])[0]
            with self.store.lock:
                s = self.store.suggestion(sid)
                if s is None:
                    return self._json({"error": "unknown id"}, 404)
                text = self.store.file_text(s)
                if text is None:
                    return self._json({"error": "file unreadable"}, 404)
                self._json({"file": s.get("file", ""), "text": text})
        elif urlparse(self.path).path == "/api/pdf":
            # the compiled PDF next to a suggestion's tex file;
            # cacheable so page jumps don't refetch it
            sid = parse_qs(urlparse(self.path).query).get("id", [""])[0]
            with self.store.lock:
                s = self.store.suggestion(sid)
                if s is None:
                    return self._json({"error": "unknown id"}, 404)
                _, pdf = self._pdf_for(s)
            if pdf is None:
                return self._json({"error": "no PDF"}, 404)
            self._send(200, pdf.read_bytes(), "application/pdf",
                       cache="max-age=300")
        elif urlparse(self.path).path == "/api/state":
            q = parse_qs(urlparse(self.path).query)
            try:
                ctx = int(q.get("ctx", ["1"])[0])
            except ValueError:
                ctx = 1
            ctx = max(0, min(ctx, 99))
            with self.store.lock:
                self.store.reload()   # pick up new agent passes live
                self._json({
                    "source": tilde(self.store.path),
                    "user": getpass.getuser(),
                    "suggestions": annotate(self.store, ctx),
                })
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)

        if self.path == "/api/decide":
            sid, status = body.get("id"), body.get("status")
            if status not in VALID_STATUSES - {"applied"}:
                return self._json({"error": "bad status"}, 400)
            with self.store.lock:
                s = self.store.suggestion(sid)
                if s is None:
                    return self._json({"error": "unknown id"}, 404)
                if s["status"] == "applied":
                    return self._json({"error": "already applied"}, 409)
                s["status"] = status
                self.store.save()
            return self._json({"ok": True})

        if self.path == "/api/suggest":
            # create a human-authored suggestion (stored in manual.json /
            # the single file — agent pass files stay read-only)
            file, old, new = body.get("file"), body.get("old"), body.get("new")
            if not (isinstance(file, str) and file
                    and isinstance(old, str) and old
                    and isinstance(new, str)):
                return self._json(
                    {"error": "file, old and new are required"}, 400)
            s = {"file": file, "old": old, "new": new}
            occ = body.get("occurrence")
            if occ is not None:
                if not isinstance(occ, int) or occ < 1:
                    return self._json({"error": "bad occurrence"}, 400)
                s["occurrence"] = occ
            if body.get("reasoning"):
                s["reasoning"] = str(body["reasoning"])
            if body.get("tags"):
                s["tags"] = [str(t) for t in body["tags"] if str(t).strip()]
            s["author"] = str(body.get("author") or getpass.getuser())
            s["date"] = now_iso()
            with self.store.lock:
                dup = self.store.suggestion_by_key(content_key(s))
                if dup:
                    return self._json({"id": dup["id"], "existing": True})
                probe = {**s, "_base": self.store.manual_base()}
                m = locate(self.store.file_text(probe), probe)
                if m.state != "ok":
                    return self._json({"error": f"anchor {m.state}"}, 400)
                created = self.store.add_manual(s)
            return self._json({"id": created["id"]})

        if self.path == "/api/reply":
            text = str(body.get("text") or "").strip()
            if not text:
                return self._json({"error": "empty reply"}, 400)
            reply = {"text": text,
                     "author": str(body.get("author")
                                   or getpass.getuser()),
                     "date": now_iso()}
            with self.store.lock:
                if self.store.add_reply(body.get("id"), reply) is None:
                    return self._json({"error": "unknown id"}, 404)
            return self._json({"ok": True})

        if self.path == "/api/reply_edit":
            ci, text = body.get("ci"), str(body.get("text") or "").strip()
            if not isinstance(ci, int):
                return self._json({"error": "bad ci"}, 400)
            with self.store.lock:
                if self.store.edit_reply(body.get("id"), ci, text) is None:
                    return self._json({"error": "unknown reply"}, 404)
            return self._json({"ok": True})

        if self.path == "/api/edit":
            fields = {}
            if "new" in body:
                if not isinstance(body["new"], str):
                    return self._json({"error": "bad new"}, 400)
                fields["new"] = body["new"]
            if "reasoning" in body:
                fields["reasoning"] = str(body["reasoning"])
            if "tags" in body:
                fields["tags"] = [str(t) for t in body["tags"]
                                  if str(t).strip()]
            if not fields:
                return self._json({"error": "nothing to edit"}, 400)
            with self.store.lock:
                s = self.store.suggestion(body.get("id"))
                if s is None:
                    return self._json({"error": "unknown id"}, 404)
                if s["status"] == "applied":
                    return self._json({"error": "already applied"}, 409)
                self.store.edit(s["id"], fields)
            return self._json({"ok": True})

        if self.path == "/api/sync":
            # synctex forward search for a suggestion's current line:
            # either jump an external viewer (--pdf-viewer template)
            # or report the PDF page for the in-UI panel
            with self.store.lock:
                s = self.store.suggestion(body.get("id"))
                if s is None:
                    return self._json({"error": "unknown id"}, 404)
                text = self.store.file_text(s)
                line = locate(text, s).line
                if not line and text and s.get("status") == "applied" \
                        and isinstance(s.get("applied_at"), int):
                    line = text.count("\n", 0, s["applied_at"]) + 1
                line = line or 1
                tex, pdf = self._pdf_for(s)
                sid = s["id"]
            if pdf is None:
                return self._json(
                    {"error": f"no PDF next to {s.get('file', '?')} — "
                              "compile it first"}, 404)
            if self.pdf_viewer:
                cmd = [t.format(line=line, tex=str(tex), pdf=str(pdf))
                       for t in shlex.split(self.pdf_viewer)]
                try:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                except OSError as e:
                    return self._json({"error": f"viewer: {e}"}, 500)
                return self._json({"mode": "viewer", "line": line})
            page = synctex_page(tex, line, pdf)
            if page is None:
                return self._json(
                    {"error": "synctex lookup failed — is the synctex "
                              "CLI installed and the PDF compiled with "
                              "-synctex=1?"}, 409)
            return self._json({"mode": "page", "page": page,
                               "line": line,
                               "url": f"/api/pdf?id={quote(sid)}"})

        if self.path == "/api/purge":
            sts = body.get("statuses") or ["applied", "rejected"]
            if not isinstance(sts, list) \
                    or set(map(str, sts)) - VALID_STATUSES:
                return self._json({"error": "bad statuses"}, 400)
            with self.store.lock:
                rep = self.store.purge(set(map(str, sts)))
            return self._json(rep)

        if self.path == "/api/revert":
            with self.store.lock:
                s = self.store.suggestion(body.get("id"))
                if s is None:
                    return self._json({"error": "unknown id"}, 404)
                if s["status"] != "applied":
                    return self._json({"error": "not applied"}, 409)
                res = self.store.revert(s["id"])
            if res is True:
                return self._json({"ok": True})
            return self._json({"error": f"cannot revert: {res}"}, 409)

        if self.path == "/api/apply":
            with self.store.lock:
                return self._json(apply_accepted(self.store))

        self._send(404, b"not found", "text/plain")

    def log_message(self, fmt, *args):  # quieter console
        pass


def serve(store: Store, port: int = 8123, open_browser: bool = False,
          pdf_viewer: str = None) -> None:
    Handler.store = store
    Handler.pdf_viewer = pdf_viewer
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"reviewing {tilde(store.path)}  →  {url}")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
