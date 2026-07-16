import json

from texreview.core import Store, content_key


def test_add_manual_dir_mode(basic_review):
    st = Store(basic_review / "review")
    got = st.add_manual({"file": "paper.tex", "old": "kernel methods",
                         "new": "RKHS methods", "tags": ["notation"]})
    assert got["id"] == "m001"
    assert got["status"] == "pending"
    doc = json.loads((basic_review / "review" / "manual.json").read_text())
    assert doc["base_dir"] == ".."
    assert doc["suggestions"][0]["id"] == "m001"
    # next one gets the next id
    got2 = st.add_manual({"file": "paper.tex", "old": "Intro line",
                          "new": "Introductory line"})
    assert got2["id"] == "m002"
    # loaded as a regular pass on a fresh Store
    st2 = Store(basic_review / "review")
    assert st2.suggestion("m001")["source"] == "manual.json"


def test_add_manual_dedupes_against_agent_pass(basic_review):
    st = Store(basic_review / "review")
    s = {"file": "paper.tex", "old": "the the", "new": "the"}
    got = st.add_manual(s)
    # identical to agent's s2 -> dedupe keeps the agent's (first) copy
    assert got["id"] == "s2"
    assert got["key"] == content_key(s)


def test_add_manual_single_mode(single_review):
    st = Store(single_review)
    got = st.add_manual({"file": "paper.tex", "old": "of of", "new": "of"})
    assert got["id"] == "m001"
    doc = json.loads(single_review.read_text())
    assert [x["id"] for x in doc["suggestions"]] == ["s1", "s2", "m001"]
    assert "_base" not in json.dumps(doc)


def test_manual_base(basic_review, single_review):
    assert Store(basic_review / "review").manual_base() \
        == basic_review.resolve()
    assert Store(single_review).manual_base() \
        == single_review.parent.resolve()
