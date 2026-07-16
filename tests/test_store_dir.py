import json

from conftest import make_pass, sug, write_json

from texreview.core import Store


def test_load_and_annotate(basic_review):
    from texreview.core import annotate
    st = Store(basic_review / "review")
    assert [s["id"] for s in st.suggestions] == ["s1", "s2", "s3"]
    rows = annotate(st)
    assert all(r["match"]["state"] == "ok" for r in rows)
    assert all(r["status"] == "pending" for r in rows)
    assert all("_base" not in r for r in rows)
    assert all(r["fkey"] for r in rows)


def test_dedupe_first_wins(manuscript):
    make_pass(manuscript, "0001-a.json",
              [sug("a1", "the the", "the", reasoning="first")])
    make_pass(manuscript, "0002-b.json",
              [sug("b1", "the the", "the", reasoning="second"),
               sug("b2", "of of", "of")])
    st = Store(manuscript / "review")
    ids = [s["id"] for s in st.suggestions]
    assert ids == ["a1", "b2"]                    # duplicate b1 dropped
    assert st.suggestions[0]["reasoning"] == "first"


def test_id_uniquification(manuscript):
    make_pass(manuscript, "0001-a.json", [sug("x", "the the", "the")])
    make_pass(manuscript, "0002-b.json", [sug("x", "of of", "of")])
    st = Store(manuscript / "review")
    assert sorted(s["id"] for s in st.suggestions) == ["x", "x'"]


def test_bare_list_pass_and_default_id(manuscript):
    write_json(manuscript / "review" / "0001-bare.json",
               [{"file": "paper.tex", "old": "the the", "new": "the"}])
    st = Store(manuscript / "review")
    assert st.suggestions[0]["id"] == "0001-bare#1"
    assert st.suggestions[0]["source"] == "0001-bare.json"


def test_base_dir(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "paper.tex").write_text("the the end")
    (tmp_path / "review").mkdir()
    write_json(tmp_path / "review" / "0001-p.json", {
        "base_dir": "../sub",
        "suggestions": [sug("s1", "the the", "the")],
    })
    st = Store(tmp_path / "review")
    assert st.file_text(st.suggestions[0]) == "the the end"


def test_skips_tool_files_and_bad_json(manuscript, capsys):
    make_pass(manuscript, "0001-a.json", [sug("a1", "the the", "the")])
    write_json(manuscript / "review" / "decisions.json", {"deadbeef": "x"})
    write_json(manuscript / "review" / "comments.json", {})
    (manuscript / "review" / "0002-broken.json").write_text("{oops")
    (manuscript / "review" / "0003-x.json.tmp").write_text("{}")
    st = Store(manuscript / "review")
    assert [s["id"] for s in st.suggestions] == ["a1"]
    assert "0002-broken.json" in capsys.readouterr().err


def test_decisions_roundtrip_and_pass_deletion(basic_review):
    st = Store(basic_review / "review")
    st.suggestion("s2")["status"] = "accepted"
    st.suggestion("s3")["status"] = "rejected"
    st.save()
    dec = json.loads(
        (basic_review / "review" / "decisions.json").read_text())
    assert set(dec.values()) == {"accepted", "rejected"}
    # decisions survive a reload and are keyed by content, not id
    st2 = Store(basic_review / "review")
    assert st2.suggestion("s2")["status"] == "accepted"
    # deleting the pass file is valid: everything just disappears
    (basic_review / "review" / "0001-pass.json").unlink()
    st3 = Store(basic_review / "review")
    assert st3.suggestions == []


def test_decision_survives_reemission(manuscript):
    make_pass(manuscript, "0001-a.json", [sug("a1", "the the", "the")])
    st = Store(manuscript / "review")
    st.suggestion("a1")["status"] = "accepted"
    st.save()
    # agent re-emits the same content under a different id / pass
    make_pass(manuscript, "0002-b.json", [sug("zz", "the the", "the")])
    st2 = Store(manuscript / "review")
    assert st2.suggestion("a1")["status"] == "accepted"


def test_file_text_containment(basic_review):
    st = Store(basic_review / "review")
    evil = dict(st.suggestions[0])
    evil["file"] = "../../../etc/passwd"
    assert st.file_text(evil) is None
