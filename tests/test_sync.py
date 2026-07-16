"""PDF serving + synctex forward search (synctex itself is mocked so
the suite runs without a TeX installation)."""

import texreview.server as srv

PDF_BYTES = b"%PDF-1.4 fake\n%%EOF\n"


def with_pdf(root):
    (root / "paper.pdf").write_bytes(PDF_BYTES)
    return root


def test_api_pdf(basic_review, make_client):
    with_pdf(basic_review)
    c = make_client(basic_review / "review")
    code, body = c.get("/api/pdf?id=s1")
    assert code == 200 and body == PDF_BYTES
    assert c.get("/api/pdf?id=zzz")[0] == 404


def test_api_pdf_missing(basic_review, make_client):
    c = make_client(basic_review / "review")
    assert c.get("/api/pdf?id=s1")[0] == 404


def test_sync_page_mode(basic_review, make_client, monkeypatch):
    with_pdf(basic_review)
    seen = {}

    def fake(tex, line, pdf):
        seen.update(tex=tex, line=line, pdf=pdf)
        return 7

    monkeypatch.setattr(srv, "synctex_page", fake)
    c = make_client(basic_review / "review")
    code, d = c.post("/api/sync", {"id": "s1"})
    assert code == 200
    assert d == {"mode": "page", "page": 7, "line": 4,
                 "url": "/api/pdf?id=s1"}
    assert seen["line"] == 4                     # s1 anchors on line 4
    assert seen["tex"].name == "paper.tex"
    assert seen["pdf"].name == "paper.pdf"


def test_sync_failures(basic_review, make_client, monkeypatch):
    c = make_client(basic_review / "review")
    assert c.post("/api/sync", {"id": "zzz"})[0] == 404
    assert c.post("/api/sync", {"id": "s1"})[0] == 404   # no PDF yet
    with_pdf(basic_review)
    monkeypatch.setattr(srv, "synctex_page", lambda *a: None)
    code, d = c.post("/api/sync", {"id": "s1"})
    assert code == 409 and "synctex" in d["error"]


def test_sync_viewer_mode(basic_review, make_client, monkeypatch):
    with_pdf(basic_review)
    calls = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            calls.append(cmd)

    monkeypatch.setattr(srv.subprocess, "Popen", FakePopen)
    c = make_client(basic_review / "review",
                    pdf_viewer="viewer --forward {line}:1:{tex} {pdf}")
    code, d = c.post("/api/sync", {"id": "s1"})
    assert code == 200 and d == {"mode": "viewer", "line": 4}
    (cmd,) = calls
    assert cmd[0] == "viewer"
    assert cmd[1] == "--forward"
    assert cmd[2].startswith("4:1:") and cmd[2].endswith("paper.tex")
    assert cmd[3].endswith("paper.pdf")


def test_synctex_page_parses_output(monkeypatch, tmp_path):
    class Out:
        stdout = "This is SyncTeX\nPage:12\nx:100.5\ny:200.2\n"

    monkeypatch.setattr(srv.shutil, "which", lambda n: "/usr/bin/synctex")
    monkeypatch.setattr(srv.subprocess, "run", lambda *a, **k: Out())
    assert srv.synctex_page(tmp_path / "a.tex", 3, tmp_path / "a.pdf") == 12
    monkeypatch.setattr(srv.shutil, "which", lambda n: None)
    assert srv.synctex_page(tmp_path / "a.tex", 3, tmp_path / "a.pdf") is None
