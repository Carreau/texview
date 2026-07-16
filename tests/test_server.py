import getpass
import http.client
import json

from conftest import make_pass, sug


def test_state_and_ctx_clamp(basic_review, make_client):
    c = make_client(basic_review / "review")
    code, d = c.get("/api/state")
    assert code == 200
    assert d["user"] == getpass.getuser()
    assert len(d["suggestions"]) == 3
    s = d["suggestions"][0]
    assert s["match"]["state"] == "ok" and s["fkey"]
    ctx1 = s["match"]["context"]
    _, d0 = c.get("/api/state?ctx=0")
    _, d3 = c.get("/api/state?ctx=3")
    _, dbad = c.get("/api/state?ctx=banana")
    assert d0["suggestions"][0]["match"]["context"].count("\n") == 0
    assert d3["suggestions"][0]["match"]["context"].count("\n") \
        > ctx1.count("\n")
    assert dbad["suggestions"][0]["match"]["context"] == ctx1


def test_index_and_404(basic_review, make_client):
    c = make_client(basic_review / "review")
    code, body = c.get("/")
    assert code == 200 and b"<!doctype html>" in body[:20]
    assert c.get("/nope")[0] == 404


def test_mathjax_traversal_guard(basic_review, make_client):
    c = make_client(basic_review / "review")
    conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=5)
    conn.request("GET", "/mathjax/../../../etc/passwd")
    assert conn.getresponse().status == 404
    conn.close()


def test_api_file(basic_review, make_client):
    c = make_client(basic_review / "review")
    code, d = c.get("/api/file?id=s1")
    assert code == 200 and d["file"] == "paper.tex"
    assert "convergence" in d["text"]
    assert c.get("/api/file?id=zzz")[0] == 404


def test_decide(basic_review, make_client):
    c = make_client(basic_review / "review")
    assert c.post("/api/decide", {"id": "s1", "status": "accepted"}) \
        == (200, {"ok": True})
    assert c.post("/api/decide", {"id": "s1", "status": "applied"})[0] == 400
    assert c.post("/api/decide", {"id": "s1", "status": "bogus"})[0] == 400
    assert c.post("/api/decide", {"id": "zzz", "status": "accepted"})[0] \
        == 404
    _, d = c.get("/api/state")
    assert {s["id"]: s["status"] for s in d["suggestions"]}["s1"] \
        == "accepted"


def test_apply_and_409_on_applied(basic_review, make_client):
    c = make_client(basic_review / "review")
    c.post("/api/decide", {"id": "s2", "status": "accepted"})
    code, rep = c.post("/api/apply", {})
    assert code == 200 and rep["applied"] == ["s2"]
    assert c.post("/api/decide", {"id": "s2", "status": "pending"})[0] == 409


def test_suggest(basic_review, make_client):
    c = make_client(basic_review / "review")
    code, d = c.post("/api/suggest", {
        "file": "paper.tex", "old": "kernel methods",
        "new": "RKHS methods", "tags": ["notation"], "reasoning": "why"})
    assert code == 200 and d["id"] == "m001"
    _, state = c.get("/api/state")
    m = [s for s in state["suggestions"] if s["id"] == "m001"][0]
    assert m["author"] == getpass.getuser() and m["date"]
    # duplicate returns the existing item
    code, d = c.post("/api/suggest", {
        "file": "paper.tex", "old": "kernel methods", "new": "RKHS methods"})
    assert (code, d.get("existing")) == (200, True)
    # validation
    assert c.post("/api/suggest",
                  {"file": "paper.tex", "old": "nope!", "new": "x"})[0] == 400
    assert c.post("/api/suggest", {"file": "paper.tex", "old": "x"})[0] == 400
    assert c.post("/api/suggest", {
        "file": "paper.tex", "old": "the", "new": "x"})[0] == 400  # ambiguous
    # author override is honored
    code, d = c.post("/api/suggest", {
        "file": "paper.tex", "old": "Intro line", "new": "First line",
        "author": "someone-else"})
    _, state = c.get("/api/state")
    m = [s for s in state["suggestions"] if s["id"] == d["id"]][0]
    assert m["author"] == "someone-else"


def test_edit_endpoint(basic_review, make_client):
    c = make_client(basic_review / "review")
    assert c.post("/api/edit", {"id": "s1", "new": "we show"}) \
        == (200, {"ok": True})
    assert c.post("/api/edit", {"id": "zzz", "new": "x"})[0] == 404
    assert c.post("/api/edit", {"id": "s1"})[0] == 400
    c.post("/api/decide", {"id": "s2", "status": "accepted"})
    c.post("/api/apply", {})
    assert c.post("/api/edit", {"id": "s2", "new": "x"})[0] == 409


def test_reply_endpoints(basic_review, make_client):
    c = make_client(basic_review / "review")
    assert c.post("/api/reply", {"id": "s1", "text": "hi"})[0] == 200
    assert c.post("/api/reply", {"id": "s1", "text": "  "})[0] == 400
    assert c.post("/api/reply", {"id": "zzz", "text": "hi"})[0] == 404
    _, state = c.get("/api/state")
    r = [s for s in state["suggestions"] if s["id"] == "s1"][0]["replies"][0]
    assert r["author"] == getpass.getuser() and r["editable"] and r["ci"] == 0
    assert c.post("/api/reply_edit",
                  {"id": "s1", "ci": 0, "text": "edited"})[0] == 200
    assert c.post("/api/reply_edit",
                  {"id": "s1", "ci": 7, "text": "x"})[0] == 404
    assert c.post("/api/reply_edit",
                  {"id": "s1", "ci": "a", "text": "x"})[0] == 400
    assert c.post("/api/reply_edit", {"id": "s1", "ci": 0, "text": ""})[0] \
        == 200
    _, state = c.get("/api/state")
    assert "replies" not in \
        [s for s in state["suggestions"] if s["id"] == "s1"][0]


def test_bad_json_body(basic_review, make_client):
    c = make_client(basic_review / "review")
    conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=5)
    conn.request("POST", "/api/decide", body=b"{not json")
    assert conn.getresponse().status == 400
    conn.close()


def test_hot_reload_picks_up_new_pass(basic_review, make_client):
    c = make_client(basic_review / "review")
    assert len(c.get("/api/state")[1]["suggestions"]) == 3
    make_pass(basic_review, "0002-late.json",
              [sug("late", "kernel methods", "kernels")])
    assert len(c.get("/api/state")[1]["suggestions"]) == 4
