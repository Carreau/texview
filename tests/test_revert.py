import json

from conftest import PAPER, make_pass, sug

from texreview.core import Store, apply_accepted


def accept_and_apply(st, *ids):
    for i in ids:
        st.suggestion(i)["status"] = "accepted"
    st.save()
    return apply_accepted(st)


def test_applied_at_recorded(basic_review):
    st = Store(basic_review / "review")
    accept_and_apply(st, "s2", "s3")
    text = (basic_review / "paper.tex").read_text()
    for sid in ("s2", "s3"):
        s = st.suggestion(sid)
        at = s["applied_at"]
        assert text[at:at + len(s["new"])] == s["new"]
    dec = json.loads(
        (basic_review / "review" / "decisions.json").read_text())
    assert all(isinstance(v, dict) and "applied_at" in v
               for v in dec.values())


def test_revert_roundtrip(basic_review):
    st = Store(basic_review / "review")
    accept_and_apply(st, "s2", "s3")
    assert st.revert("s2") is True
    text = (basic_review / "paper.tex").read_text()
    assert "the the" in text                   # s2 undone
    assert "of of" not in text                 # s3 still applied
    st2 = Store(basic_review / "review")
    assert st2.suggestion("s2")["status"] == "pending"
    dec = json.loads(
        (basic_review / "review" / "decisions.json").read_text())
    key2 = st2.suggestion("s2")["key"]
    assert key2 not in dec                     # pending, no leftover state
    # and it can be re-applied
    st2.suggestion("s2")["status"] = "accepted"
    st2.save()
    rep = apply_accepted(st2)
    assert rep["applied"] == ["s2"]
    assert "the the" not in (basic_review / "paper.tex").read_text()


def test_revert_guards(basic_review):
    st = Store(basic_review / "review")
    assert st.revert("s2") is None             # not applied yet
    assert st.revert("nope") is None


def test_revert_deleted_text(manuscript):
    make_pass(manuscript, "0001-d.json", [sug("d1", " the the", "")])
    st = Store(manuscript / "review")
    accept_and_apply(st, "d1")
    assert st.revert("d1") == "deleted-text"
    assert st.suggestion("d1")["status"] == "applied"


def test_revert_falls_back_to_unique_match(basic_review):
    st = Store(basic_review / "review")
    accept_and_apply(st, "s1")                 # new: "we show that", unique
    # corrupt the hint: revert must fall back to searching for `new`
    dec_path = basic_review / "review" / "decisions.json"
    dec = json.loads(dec_path.read_text())
    for v in dec.values():
        v["applied_at"] = 0
    dec_path.write_text(json.dumps(dec))
    st2 = Store(basic_review / "review")
    assert st2.revert("s1") is True
    assert "we shows that" in (basic_review / "paper.tex").read_text()


def test_revert_ambiguous_without_hint(manuscript):
    (manuscript / "paper.tex").write_text("aaa bbb aaa ccc\n")
    make_pass(manuscript, "0001-p.json", [sug("r1", "bbb", "aaa")])
    st = Store(manuscript / "review")
    accept_and_apply(st, "r1")                 # text: "aaa aaa aaa ccc"
    dec_path = manuscript / "review" / "decisions.json"
    dec = json.loads(dec_path.read_text())
    for v in dec.values():
        del v["applied_at"]
    dec_path.write_text(json.dumps(dec))
    st2 = Store(manuscript / "review")
    assert st2.revert("r1") == "ambiguous"
    # with the hint intact it works despite the ambiguity
    st3 = Store(manuscript / "review")
    st3.suggestion("r1")["applied_at"] = 4
    assert st3.revert("r1") is True
    assert (manuscript / "paper.tex").read_text() == "aaa bbb aaa ccc\n"


def test_revert_single_mode(single_review):
    st = Store(single_review)
    st.suggestion("s2")["status"] = "accepted"
    st.save()
    apply_accepted(st)
    doc = json.loads(single_review.read_text())
    by_id = {s["id"]: s for s in doc["suggestions"]}
    assert isinstance(by_id["s2"]["applied_at"], int)
    st2 = Store(single_review)
    assert st2.revert("s2") is True
    assert "the the" in (single_review.parent / "paper.tex").read_text()
    doc = json.loads(single_review.read_text())
    by_id = {s["id"]: s for s in doc["suggestions"]}
    assert by_id["s2"]["status"] == "pending"
    assert "applied_at" not in by_id["s2"]


def test_revert_endpoint(basic_review, make_client):
    c = make_client(basic_review / "review")
    assert c.post("/api/revert", {"id": "zzz"})[0] == 404
    assert c.post("/api/revert", {"id": "s2"})[0] == 409  # not applied
    c.post("/api/decide", {"id": "s2", "status": "accepted"})
    c.post("/api/apply", {})
    assert c.post("/api/revert", {"id": "s2"}) == (200, {"ok": True})
    _, d = c.get("/api/state")
    assert {s["id"]: s["status"] for s in d["suggestions"]}["s2"] \
        == "pending"
