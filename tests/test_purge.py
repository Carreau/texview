import json

from conftest import make_pass, sug, write_json

from texreview.cli import main
from texreview.core import Store, apply_accepted


def resolve_some(root):
    """apply s2, reject s3, leave s1 pending; add a reply to each."""
    st = Store(root / "review")
    st.suggestion("s2")["status"] = "accepted"
    st.save()
    apply_accepted(st)
    st.suggestion("s3")["status"] = "rejected"
    st.save()
    st.add_reply("s1", {"text": "keep me", "author": "me", "date": "2026"})
    st.add_reply("s3", {"text": "drop me", "author": "me", "date": "2026"})
    return Store(root / "review")


def test_purge_dir_mode(basic_review):
    st = resolve_some(basic_review)
    rep = st.purge()
    assert sorted(rep["removed"]) == ["s2", "s3"]
    assert rep["rewritten"] == ["0001-pass.json"]
    st2 = Store(basic_review / "review")
    assert [s["id"] for s in st2.suggestions] == ["s1"]
    # decisions + comments pruned of dead keys, survivors kept
    dec = json.loads(
        (basic_review / "review" / "decisions.json").read_text())
    assert dec == {}
    comments = json.loads(
        (basic_review / "review" / "comments.json").read_text())
    assert len(comments) == 1
    assert st2.suggestion("s1")["replies"][0]["text"] == "keep me"
    # applied text stays applied in the manuscript
    assert "the the" not in (basic_review / "paper.tex").read_text()


def test_purge_dry_run(basic_review):
    st = resolve_some(basic_review)
    before = (basic_review / "review" / "0001-pass.json").read_bytes()
    rep = st.purge(dry_run=True)
    assert sorted(rep["removed"]) == ["s2", "s3"]
    assert (basic_review / "review" / "0001-pass.json").read_bytes() \
        == before
    assert len(Store(basic_review / "review").suggestions) == 3


def test_purge_deletes_emptied_pass(manuscript):
    make_pass(manuscript, "0001-a.json", [sug("a1", "the the", "the")])
    make_pass(manuscript, "0002-b.json", [sug("b1", "of of", "of")])
    st = Store(manuscript / "review")
    st.suggestion("a1")["status"] = "rejected"
    st.save()
    rep = st.purge()
    assert rep["deleted"] == ["0001-a.json"]
    assert not (manuscript / "review" / "0001-a.json").exists()
    assert (manuscript / "review" / "0002-b.json").exists()


def test_purge_drops_dangling_replies(basic_review):
    make_pass(basic_review, "0002-resp.json", [], replies=[
        {"to": "s3", "text": "about the doomed one"},
        {"to": "s1", "text": "about the survivor"},
    ])
    st = Store(basic_review / "review")
    st.suggestion("s3")["status"] = "rejected"
    st.save()
    st = Store(basic_review / "review")
    rep = st.purge()
    assert "0002-resp.json" in rep["rewritten"]
    doc = json.loads(
        (basic_review / "review" / "0002-resp.json").read_text())
    assert [r["to"] for r in doc["replies"]] == ["s1"]


def test_purge_status_filter(basic_review):
    st = resolve_some(basic_review)
    rep = st.purge(statuses={"rejected"})
    assert rep["removed"] == ["s3"]
    st2 = Store(basic_review / "review")
    assert sorted(s["id"] for s in st2.suggestions) == ["s1", "s2"]
    assert st2.suggestion("s2")["status"] == "applied"   # decision kept


def test_purge_bare_list_pass(manuscript):
    write_json(manuscript / "review" / "0001-bare.json",
               [{"file": "paper.tex", "old": "the the", "new": "the"},
                {"file": "paper.tex", "old": "of of", "new": "of"}])
    st = Store(manuscript / "review")
    st.suggestions[0]["status"] = "rejected"
    st.save()
    st = Store(manuscript / "review")
    st.purge()
    doc = json.loads(
        (manuscript / "review" / "0001-bare.json").read_text())
    assert isinstance(doc, list) and len(doc) == 1
    assert doc[0]["old"] == "of of"


def test_purge_single_mode(single_review):
    st = Store(single_review)
    st.suggestion("s1")["status"] = "rejected"
    st.save()
    rep = st.purge()
    assert rep["removed"] == ["s1"]
    doc = json.loads(single_review.read_text())
    assert [s["id"] for s in doc["suggestions"]] == ["s2"]


def test_purge_endpoint(basic_review, make_client):
    resolve_some(basic_review)
    c = make_client(basic_review / "review")
    assert c.post("/api/purge", {"statuses": ["bogus"]})[0] == 400
    code, rep = c.post("/api/purge", {})
    assert code == 200 and sorted(rep["removed"]) == ["s2", "s3"]
    _, d = c.get("/api/state")
    assert [s["id"] for s in d["suggestions"]] == ["s1"]


def test_purge_cli(basic_review, capsys):
    resolve_some(basic_review)
    assert main(["purge", str(basic_review / "review"), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would remove 2" in out
    assert main(["purge", str(basic_review / "review")]) == 0
    out = capsys.readouterr().out
    assert "removed 2: s2, s3" in out and "rewrote 0001-pass.json" in out
    assert main(["purge", str(basic_review / "review"),
                 "--status", "bogus"]) == 1
