import json

from texreview.core import Store, apply_accepted


def test_status_written_back(single_review):
    st = Store(single_review)
    st.suggestion("s1")["status"] = "accepted"
    st.save()
    doc = json.loads(single_review.read_text())
    by_id = {s["id"]: s for s in doc["suggestions"]}
    assert by_id["s1"]["status"] == "accepted"
    # regression: normalization must not leak internals into the file
    dump = single_review.read_text()
    for forbidden in ("_base", '"key"', '"source"'):
        assert forbidden not in dump


def test_apply_single_mode(single_review):
    st = Store(single_review)
    st.suggestion("s1")["status"] = "accepted"
    st.suggestion("s2")["status"] = "accepted"
    st.save()
    rep = apply_accepted(st)
    assert sorted(rep["applied"]) == ["s1", "s2"]
    text = (single_review.parent / "paper.tex").read_text()
    assert "we show that the convergence" in text
    doc = json.loads(single_review.read_text())
    assert {s["status"] for s in doc["suggestions"]} == {"applied"}
    assert (single_review.parent / "paper.tex.bak").exists()


def test_edit_single_mode(single_review):
    st = Store(single_review)
    st.edit("s1", {"new": "we prove that", "reasoning": "stronger"})
    doc = json.loads(single_review.read_text())
    by_id = {s["id"]: s for s in doc["suggestions"]}
    assert by_id["s1"]["new"] == "we prove that"
    assert by_id["s1"]["reasoning"] == "stronger"
    assert Store(single_review).suggestion("s1")["new"] == "we prove that"
