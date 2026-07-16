import json

from conftest import PAPER, make_pass, sug

import texreview.core as core
from texreview.core import Store, apply_accepted


def accept(st, *ids):
    for i in ids:
        st.suggestion(i)["status"] = "accepted"
    st.save()


def test_two_edits_same_line_right_to_left(basic_review):
    st = Store(basic_review / "review")
    accept(st, "s2", "s3")
    rep = apply_accepted(st)
    assert sorted(rep["applied"]) == ["s2", "s3"]
    assert rep["skipped"] == []
    line = (basic_review / "paper.tex").read_text().splitlines()[3]
    assert "the convergence rate of the estimator" in line
    assert "the the" not in line and "of of" not in line


def test_bak_created_once_and_pass_files_untouched(basic_review):
    before = (basic_review / "review" / "0001-pass.json").read_bytes()
    st = Store(basic_review / "review")
    accept(st, "s2")
    apply_accepted(st)
    bak = basic_review / "paper.tex.bak"
    assert bak.read_text() == PAPER
    accept(st, "s3")
    apply_accepted(st)
    assert bak.read_text() == PAPER            # still the first snapshot
    assert (basic_review / "review" / "0001-pass.json").read_bytes() \
        == before


def test_only_accepted_ok_applied(basic_review, manuscript=None):
    root = basic_review
    make_pass(root, "0002-bad.json", [
        sug("miss", "not in the file", "x"),
        sug("ambi", "the", "teh"),             # many matches, no occurrence
    ])
    st = Store(root / "review")
    accept(st, "miss", "ambi", "s2")
    st.suggestion("s3")["status"] = "rejected"
    st.save()
    rep = apply_accepted(st)
    assert rep["applied"] == ["s2"]
    assert {(s["id"], s["reason"]) for s in rep["skipped"]} \
        == {("miss", "missing"), ("ambi", "ambiguous")}
    # rejected s3's text is untouched
    assert "of of" in (root / "paper.tex").read_text()


def test_overlap_between_accepted_skipped(basic_review):
    make_pass(basic_review, "0002-wide.json", [
        sug("wide", "shows that the the convergence", "overlaps s1+s2"),
    ])
    st = Store(basic_review / "review")
    accept(st, "s2", "wide")
    rep = apply_accepted(st)
    assert rep["applied"] == []
    assert {s["reason"] for s in rep["skipped"]} == {"overlap"}
    # overlap only matters between accepted: alone, wide applies fine
    st2 = Store(basic_review / "review")
    st2.suggestion("s2")["status"] = "rejected"
    st2.save()
    rep2 = apply_accepted(st2)
    assert rep2["applied"] == ["wide"]


def test_shifted_race_guard(basic_review, monkeypatch):
    st = Store(basic_review / "review")
    accept(st, "s2")
    orig = core.locate

    def stale(text, s, ctx=1):
        m = orig(text, s, ctx)
        if s.get("id") == "s2" and m.state == "ok":
            m.start += 1                       # simulate a stale offset
            m.end += 1
        return m

    monkeypatch.setattr(core, "locate", stale)
    rep = apply_accepted(st)
    assert rep["applied"] == []
    assert rep["skipped"] == [{"id": "s2", "reason": "shifted"}]
    assert "the the" in (basic_review / "paper.tex").read_text()


def test_applied_status_persisted(basic_review):
    st = Store(basic_review / "review")
    accept(st, "s2")
    apply_accepted(st)
    st2 = Store(basic_review / "review")
    assert st2.suggestion("s2")["status"] == "applied"
    # applying again is a no-op
    assert apply_accepted(st2) == {"applied": [], "skipped": []}


def test_apply_uses_edited_new(basic_review):
    st = Store(basic_review / "review")
    st.edit("s1", {"new": "we demonstrate that"})
    accept(st, "s1")
    rep = apply_accepted(st)
    assert rep["applied"] == ["s1"]
    assert "we demonstrate that" in (basic_review / "paper.tex").read_text()


def test_empty_new_deletes(manuscript):
    make_pass(manuscript, "0001-d.json",
              [sug("d1", " the the", "")])
    st = Store(manuscript / "review")
    accept(st, "d1")
    apply_accepted(st)
    text = (manuscript / "paper.tex").read_text()
    assert "shows that convergence" in text
