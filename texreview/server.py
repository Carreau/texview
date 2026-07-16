"""Localhost HTTP server for the review UI."""

from __future__ import annotations

import getpass
import json
import logging
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


log = logging.getLogger("texreview")


def _synctex_parse(stdout: str):
    page = x = y = None
    for ln in stdout.splitlines():       # first result block wins
        try:
            if page is None and ln.startswith("Page:"):
                page = int(ln.split(":", 1)[1])
            elif page is not None and x is None and ln.startswith("x:"):
                x = float(ln.split(":", 1)[1])
            elif page is not None and y is None and ln.startswith("y:"):
                y = float(ln.split(":", 1)[1])
        except ValueError:
            return None
        if page is not None and x is not None and y is not None:
            break
    if page is None:
        return None
    return {"page": page, "x": x or 0.0, "y": y or 0.0}


def synctex_view(tex: Path, line: int, pdf: Path):
    """Forward search via the synctex CLI: source line -> PDF position.
    Returns {"page": int, "x": float, "y": float} (points, y from the
    top of the page) on success, or an error-message string.

    The .synctex.gz records input names as TeX saw them (often
    "./sub/file.tex" relative to the compile directory), so the
    absolute path may not match — retry with relative forms.
    """
    exe = shutil.which("synctex")
    if exe is None:
        return "synctex CLI not found on PATH (it ships with TeX Live)"
    names = [str(tex)]
    try:
        rel = tex.relative_to(pdf.parent.resolve())
        names += [str(rel), f"./{rel}"]
    except ValueError:
        pass
    for name in names:
        cmd = [exe, "view", "-i", f"{max(1, line)}:1:{name}",
               "-o", str(pdf)]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=10)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.debug("synctex: %s -> %s", " ".join(cmd), e)
            return f"synctex failed to run: {e}"
        loc = _synctex_parse(out.stdout)
        log.debug("synctex: %s -> %s (rc=%s)\nstdout: %.400s"
                  "\nstderr: %.200s", " ".join(cmd), loc,
                  out.returncode, out.stdout, out.stderr)
        if loc is not None:
            return loc
    return (f"no synctex result for {tex.name}:{line} — was "
            f"{pdf.name} compiled with -synctex=1?")


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

    @staticmethod
    def _has_synctex(p: Path) -> bool:
        return (p.with_suffix(".synctex.gz").is_file()
                or p.with_suffix(".synctex").is_file())

    def _pdf_candidates(self, s):
        """PDFs under the manuscript root (rel paths, capped) — the
        reviewed file may be \\input into a root document whose PDF
        has a different name. Ranked by likelihood of being the
        compiled document, not path order, so a tree full of
        figures/*.pdf cannot crowd out the root PDF: sibling
        .synctex.gz first, then sibling .tex, then shallower paths."""
        base = s["_base"]
        scored = []
        for p in base.rglob("*.pdf"):
            rp = p.relative_to(base)
            if any(part.startswith(".") for part in rp.parts):
                continue
            scored.append((not self._has_synctex(p),
                           not p.with_suffix(".tex").is_file(),
                           len(rp.parts), str(rp)))
        scored.sort()
        return [rp for *_, rp in scored[:20]]

    def _pdf_for(self, s, choice=None):
        """(abs tex path, abs pdf path or None) for a suggestion.
        `choice` is a client-selected rel path; otherwise prefer
        <stem>.pdf next to the tex file, else a single unambiguous
        PDF anywhere under the root."""
        base = s["_base"]
        tex = (base / s.get("file", "")).resolve()
        if choice:
            p = (base / choice).resolve()
            if str(p).startswith(str(base)) and p.suffix == ".pdf" \
                    and p.is_file():
                return tex, p
            return tex, None
        pdf = tex.with_suffix(".pdf")
        if str(pdf).startswith(str(base)) and pdf.is_file():
            return tex, pdf
        cands = self._pdf_candidates(s)
        if len(cands) == 1:
            return tex, (base / cands[0]).resolve()
        # several PDFs (figures etc.): a unique one with synctex data
        # is unambiguously the compiled document
        synced = [c for c in cands
                  if self._has_synctex((base / c).resolve())]
        if len(synced) == 1:
            return tex, (base / synced[0]).resolve()
        return tex, None

    def _json(self, obj, code: int = 200) -> None:
        if code >= 400:
            log.debug("%s %s -> %s %s", self.command, self.path, code,
                      obj)
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            page = files("texreview").joinpath(
                "static/index.html").read_bytes()
            self._send(200, page, "text/html; charset=utf-8")
        elif self.path.startswith(("/mathjax/", "/pdfjs/")):
            # vendored assets: a copy in the working directory wins,
            # then whatever ships inside the package (PDF.js does);
            # 404 lets the page fall back to the CDN (MathJax)
            prefix = self.path.split("/", 2)[1]
            rel = self.path[len(prefix) + 2:].split("?", 1)[0]
            root = (Path.cwd() / prefix).resolve()
            p = (root / rel).resolve()
            body = None
            if str(p).startswith(str(root)) and p.is_file():
                body = p.read_bytes()
            elif ".." not in rel.split("/"):
                res = files("texreview").joinpath(
                    f"static/{prefix}/{rel}")
                if res.is_file():
                    body = res.read_bytes()
            if body is not None:
                ctype = (mimetypes.guess_type(rel)[0]
                         or "application/octet-stream")
                self._send(200, body, ctype, cache="max-age=3600")
            else:
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
            q = parse_qs(urlparse(self.path).query)
            sid = q.get("id", [""])[0]
            with self.store.lock:
                s = self.store.suggestion(sid)
                if s is None:
                    return self._json({"error": "unknown id"}, 404)
                _, pdf = self._pdf_for(s, q.get("pdf", [None])[0])
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
                choice = body.get("pdf") or None
                tex, pdf = self._pdf_for(s, choice)
                cands = self._pdf_candidates(s)
                sid = s["id"]
            if pdf is None:
                msg = ("pick a PDF (the reviewed file may be \\input "
                       "into a root document)" if cands
                       else f"no PDF found for {s.get('file', '?')} — "
                            "compile it first")
                return self._json({"error": msg, "pdfs": cands}, 404)
            rel = str(pdf.relative_to(s["_base"]))
            if self.pdf_viewer:
                cmd = [t.format(line=line, tex=str(tex), pdf=str(pdf))
                       for t in shlex.split(self.pdf_viewer)]
                try:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                except OSError as e:
                    return self._json({"error": f"viewer: {e}"}, 500)
                return self._json({"mode": "viewer", "line": line,
                                   "pdf": rel, "pdfs": cands})
            loc = synctex_view(tex, line, pdf)
            if isinstance(loc, str):
                return self._json({"error": loc, "pdfs": cands,
                                   "pdf": rel}, 409)
            return self._json({"mode": "page", "line": line,
                               "url": f"/api/pdf?id={quote(sid)}"
                                      f"&pdf={quote(rel)}",
                               "pdf": rel, "pdfs": cands,
                               "pdfkey": str(pdf), **loc})

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

    def log_message(self, fmt, *args):  # quiet unless --debug
        log.debug("%s - %s", self.address_string(), fmt % args)


def serve(store: Store, port: int = 8123, open_browser: bool = False,
          pdf_viewer: str = None, debug: bool = False) -> None:
    Handler.store = store
    Handler.pdf_viewer = pdf_viewer
    if debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s.%(msecs)03d %(message)s",
            datefmt="%H:%M:%S")
        logging.getLogger("texreview").setLevel(logging.DEBUG)
        log.debug("debug logging enabled")
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
