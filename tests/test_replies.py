import json

from conftest import make_pass, sug, write_json

from texreview.core import Store


def test_pass_file_replies_by_id_and_key(basic_review):
    st = Store(basic_review / "review")
    key = st.suggestion("s2")["key"]
    make_pass(basic_review, "0002-resp.json", [], replies=[
        {"to": "s1", "text": "by id", "author": "agent",
         "date": "2026-01-01T00:00:00Z"},
        {"to": key, "text": "by key", "author": "agent",
         "date": "2026-01-02T00:00:00Z"},
        {"to": "nonexistent", "text": "dropped"},
        {"to": "s1"},                            # no text -> dropped
    ])
    st2 = Store(basic_review / "review")
    assert [r["text"] for r in st2.suggestion("s1")["replies"]] == ["by id"]
    assert [r["text"] for r in st2.suggestion("s2")["replies"]] == ["by key"]
    # pass-file replies are not editable
    assert "editable" not in st2.suggestion("s1")["replies"][0]


def test_add_reply_and_date_sort(basic_review):
    make_pass(basic_review, "0002-resp.json", [], replies=[
        {"to": "s1", "text": "later", "author": "agent",
         "date": "2026-12-31T00:00:00Z"}])
    st = Store(basic_review / "review")
    st.add_reply("s1", {"text": "earlier", "author": "me",
                        "date": "2026-01-01T00:00:00Z"})
    reps = Store(basic_review / "review").suggestion("s1")["replies"]
    assert [r["text"] for r in reps] == ["earlier", "later"]
    comments = json.loads(
        (basic_review / "review" / "comments.json").read_text())
    (lst,) = comments.values()
    assert lst[0]["author"] == "me"
    # human reply is editable, with its comments.json index
    mine = [r for r in reps if r.get("editable")]
    assert mine and mine[0]["ci"] == 0


def test_add_reply_unknown(basic_review):
    st = Store(basic_review / "review")
    assert st.add_reply("nope", {"text": "x"}) is None


def test_edit_and_delete_reply(basic_review):
    st = Store(basic_review / "review")
    st.add_reply("s1", {"text": "one", "author": "me", "date": "2026-01-01"})
    st.add_reply("s1", {"text": "two", "author": "me", "date": "2026-01-02"})
    assert st.edit_reply("s1", 0, "one, revised") is True
    reps = st.suggestion("s1")["replies"]
    assert [r["text"] for r in reps] == ["one, revised", "two"]
    # delete via empty text; indices of survivors shift down
    assert st.edit_reply("s1", 0, "") is True
    reps = st.suggestion("s1")["replies"]
    assert [(r["text"], r["ci"]) for r in reps] == [("two", 0)]
    # deleting the last reply prunes the key entirely
    assert st.edit_reply("s1", 0, "") is True
    comments = json.loads(
        (basic_review / "review" / "comments.json").read_text())
    assert comments == {}
    # out of range / unknown id
    assert st.edit_reply("s1", 5, "x") is None
    assert st.edit_reply("nope", 0, "x") is None


def test_single_mode_inline_replies(single_review):
    st = Store(single_review)
    st.add_reply("s1", {"text": "hello", "author": "me", "date": "2026"})
    doc = json.loads(single_review.read_text())
    assert doc["suggestions"][0]["replies"][0]["text"] == "hello"
    reps = Store(single_review).suggestion("s1")["replies"]
    assert reps[0]["editable"] is True and reps[0]["ci"] == 0
    st2 = Store(single_review)
    assert st2.edit_reply("s1", 0, "") is True
    doc = json.loads(single_review.read_text())
    assert "replies" not in doc["suggestions"][0]
