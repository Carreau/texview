import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from texreview import server as srv
from texreview.core import Store

PAPER = """\
\\documentclass{article}
\\begin{document}
Intro line one.
In this paper we shows that the the convergence rate of of the estimator
is optimal, and we discuss it's implications for kernel methods.
Let $x \\in R^d$ denote the input and $f(x)$ the target.
\\end{document}
"""


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1), encoding="utf-8")


def sug(id, old, new, **kw):
    return {"id": id, "file": "paper.tex", "old": old, "new": new, **kw}


def make_pass(root: Path, name: str, suggestions, replies=None,
              base_dir=".."):
    doc = {"version": 1, "base_dir": base_dir, "suggestions": suggestions}
    if replies is not None:
        doc["replies"] = replies
    write_json(root / "review" / name, doc)


@pytest.fixture
def manuscript(tmp_path):
    """A manuscript root with paper.tex and an empty review/ dir."""
    (tmp_path / "paper.tex").write_text(PAPER, encoding="utf-8")
    (tmp_path / "review").mkdir()
    return tmp_path


@pytest.fixture
def basic_review(manuscript):
    """manuscript + one pass with three resolvable suggestions."""
    make_pass(manuscript, "0001-pass.json", [
        sug("s1", "we shows that", "we show that", reasoning="Agreement."),
        sug("s2", "the the", "the", tags=["typo"]),
        sug("s3", "of of", "of", tags=["typo", "grammar"]),
    ])
    return manuscript


@pytest.fixture
def single_review(tmp_path):
    """Single-file mode: suggestions.json next to paper.tex."""
    (tmp_path / "paper.tex").write_text(PAPER, encoding="utf-8")
    write_json(tmp_path / "suggestions.json", {
        "base_dir": ".",
        "suggestions": [
            sug("s1", "we shows that", "we show that"),
            sug("s2", "the the", "the"),
        ],
    })
    return tmp_path / "suggestions.json"


class Client:
    def __init__(self, port):
        self.port = port

    def _req(self, method, path, obj=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(obj).encode() if obj is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                body = r.read()
                code = r.status
                ctype = r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            body = e.read()
            code = e.code
            ctype = e.headers.get("Content-Type", "")
        if "json" in ctype:
            return code, json.loads(body or b"{}")
        return code, body

    def get(self, path):
        return self._req("GET", path)

    def post(self, path, obj):
        return self._req("POST", path, obj)


@pytest.fixture
def make_client():
    """Factory: start the HTTP server for a review target, yield Client."""
    servers = []

    def _make(target: Path) -> Client:
        srv.Handler.store = Store(Path(target))
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return Client(httpd.server_address[1])

    yield _make
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()
