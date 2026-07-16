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
        return {"page": 7, "x": 133.7, "y": 402.5}

    monkeypatch.setattr(srv, "synctex_view", fake)
    c = make_client(basic_review / "review")
    code, d = c.post("/api/sync", {"id": "s1"})
    assert code == 200
    assert d["mode"] == "page" and d["page"] == 7
    assert d["y"] == 402.5 and d["line"] == 4
    assert d["url"] == "/api/pdf?id=s1&pdf=paper.pdf"
    assert d["pdf"] == "paper.pdf" and d["pdfs"] == ["paper.pdf"]
    assert d["pdfkey"].endswith("paper.pdf")
    assert seen["line"] == 4                     # s1 anchors on line 4
    assert seen["tex"].name == "paper.tex"
    assert seen["pdf"].name == "paper.pdf"


def test_sync_failures(basic_review, make_client, monkeypatch):
    c = make_client(basic_review / "review")
    assert c.post("/api/sync", {"id": "zzz"})[0] == 404
    assert c.post("/api/sync", {"id": "s1"})[0] == 404   # no PDF yet
    with_pdf(basic_review)
    monkeypatch.setattr(srv, "synctex_view",
                        lambda *a: "no synctex result for paper.tex:4")
    code, d = c.post("/api/sync", {"id": "s1"})
    assert code == 409 and "synctex" in d["error"]
    assert d["pdf"] == "paper.pdf"        # selector still populated


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
    assert code == 200
    assert d == {"mode": "viewer", "line": 4,
                 "pdf": "paper.pdf", "pdfs": ["paper.pdf"]}
    (cmd,) = calls
    assert cmd[0] == "viewer"
    assert cmd[1] == "--forward"
    assert cmd[2].startswith("4:1:") and cmd[2].endswith("paper.tex")
    assert cmd[3].endswith("paper.pdf")


def test_sync_pdf_discovery_and_choice(basic_review, make_client,
                                       monkeypatch):
    monkeypatch.setattr(srv, "synctex_view",
                        lambda *a: {"page": 1, "x": 0.0, "y": 0.0})
    c = make_client(basic_review / "review")
    # no <stem>.pdf, but a single root PDF elsewhere -> auto-picked
    (basic_review / "main.pdf").write_bytes(PDF_BYTES)
    code, d = c.post("/api/sync", {"id": "s1"})
    assert code == 200 and d["pdf"] == "main.pdf"
    assert d["url"] == "/api/pdf?id=s1&pdf=main.pdf"
    # two candidates and no choice -> 404 with the list
    (basic_review / "other.pdf").write_bytes(PDF_BYTES)
    code, d = c.post("/api/sync", {"id": "s1"})
    assert code == 404 and d["pdfs"] == ["main.pdf", "other.pdf"]
    # explicit choice resolves it
    code, d = c.post("/api/sync", {"id": "s1", "pdf": "other.pdf"})
    assert code == 200 and d["pdf"] == "other.pdf"
    # a bogus/escaping choice is rejected, candidates still offered
    code, d = c.post("/api/sync", {"id": "s1", "pdf": "../../x.pdf"})
    assert code == 404 and d["pdfs"] == ["main.pdf", "other.pdf"]
    # /api/pdf honors the same choice param
    code, body = c.get("/api/pdf?id=s1&pdf=other.pdf")
    assert code == 200 and body == PDF_BYTES
    # <stem>.pdf still wins over discovery when present
    (basic_review / "paper.pdf").write_bytes(PDF_BYTES)
    code, d = c.post("/api/sync", {"id": "s1"})
    assert code == 200 and d["pdf"] == "paper.pdf"


def test_sync_figures_dont_crowd_out_root(basic_review, make_client,
                                          monkeypatch):
    """A tree full of figures/*.pdf must neither hide the root PDF
    behind the candidate cap nor make the choice ambiguous when the
    root PDF has synctex data."""
    monkeypatch.setattr(srv, "synctex_view",
                        lambda *a: {"page": 1, "x": 0.0, "y": 0.0})
    figs = basic_review / "figures"
    figs.mkdir()
    for i in range(30):                      # more than the cap
        (figs / f"fig{i:02d}.pdf").write_bytes(PDF_BYTES)
    (basic_review / "main.pdf").write_bytes(PDF_BYTES)
    (basic_review / "main.synctex.gz").write_bytes(b"gz")
    c = make_client(basic_review / "review")
    code, d = c.post("/api/sync", {"id": "s1"})
    assert code == 200
    assert d["pdf"] == "main.pdf"            # auto-picked despite crowd
    assert d["pdfs"][0] == "main.pdf"        # and ranked first
    assert len(d["pdfs"]) == 20              # capped, root included
    # without synctex data the choice is ambiguous -> 404 + ranked list
    (basic_review / "main.synctex.gz").unlink()
    code, d = c.post("/api/sync", {"id": "s1"})
    assert code == 404
    assert d["pdfs"][0] == "main.pdf"        # root still ranked first


class FakeOut:
    returncode = 0
    stderr = ""

    def __init__(self, stdout):
        self.stdout = stdout


def test_synctex_view_parses_output(monkeypatch, tmp_path):
    Out = lambda *a, **k: FakeOut(  # noqa: E731
        "This is SyncTeX\nOutput:a.pdf\nPage:12\nx:100.5\n"
        "y:200.25\nh:90\nv:210\nPage:13\nx:1\ny:2\n")

    monkeypatch.setattr(srv.shutil, "which", lambda n: "/usr/bin/synctex")
    monkeypatch.setattr(srv.subprocess, "run", Out)
    assert srv.synctex_view(tmp_path / "a.tex", 3, tmp_path / "a.pdf") \
        == {"page": 12, "x": 100.5, "y": 200.25}
    # no result -> explanatory message naming file and line
    monkeypatch.setattr(srv.subprocess, "run",
                        lambda *a, **k: FakeOut("This is SyncTeX\n"))
    err = srv.synctex_view(tmp_path / "a.tex", 3, tmp_path / "a.pdf")
    assert isinstance(err, str) and "a.tex:3" in err \
        and "-synctex=1" in err
    # missing CLI -> distinct message
    monkeypatch.setattr(srv.shutil, "which", lambda n: None)
    err = srv.synctex_view(tmp_path / "a.tex", 3, tmp_path / "a.pdf")
    assert isinstance(err, str) and "not found" in err


def test_synctex_view_retries_relative_names(monkeypatch, tmp_path):
    """Absolute input paths often miss (synctex records './sub/x.tex');
    the relative forms must be tried before giving up."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd[3])                 # the -i argument
        return FakeOut("Page:2\nx:1\ny:3\n"
                       if cmd[3].startswith("5:1:./") else "nothing\n")

    monkeypatch.setattr(srv.shutil, "which", lambda n: "/usr/bin/synctex")
    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    (tmp_path / "sub").mkdir()
    tex = (tmp_path / "sub" / "intro.tex").resolve()
    pdf = (tmp_path / "main.pdf").resolve()
    loc = srv.synctex_view(tex, 5, pdf)
    assert loc == {"page": 2, "x": 1.0, "y": 3.0}
    assert calls == [f"5:1:{tex}", "5:1:sub/intro.tex",
                     "5:1:./sub/intro.tex"]
