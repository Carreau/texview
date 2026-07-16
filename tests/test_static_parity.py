"""Parity between the in-browser (local mode) engine and the Python
core: content keys and anchor matching must agree exactly, otherwise
decisions.json files produced in the browser would not interoperate
with the CLI. Extracts the LOCAL-CORE block from index.html and runs
it under node; skipped when node is unavailable."""

import json
import re
import shutil
import subprocess
from importlib.resources import files

import pytest

from texreview.core import content_key, locate

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node not found")

CASES = [
    {"file": "paper.tex", "old": "the the", "new": "the"},
    {"file": "paper.tex", "old": "foo", "new": "bar", "occurrence": 2},
    {"file": "sub/x.tex", "old": "a \"quoted\" bit\nover two lines",
     "new": ""},
    {"file": "paper.tex", "old": "unicode — ééé ∀x", "new": "π"},
    {"file": "paper.tex", "old": "tab\there", "new": "x",
     "occurrence": 1},
]

TEXT = "l1\nl2 foo bar\nl3 foo\nl4 unicode — ééé ∀x end\nl5"

LOCATE_CASES = [
    ({"old": "foo"}, 1),
    ({"old": "foo", "occurrence": 2}, 0),
    ({"old": "foo", "occurrence": 9}, 1),
    ({"old": "nope"}, 2),
    ({"old": ""}, 0),
    ({"old": "bar\nl3"}, 3),
    ({"old": "unicode — ééé"}, 100),
]


def _extract(begin, end):
    html = files("texreview").joinpath("static/index.html") \
        .read_text(encoding="utf-8")
    m = re.search(re.escape(begin) + "(.*?)" + re.escape(end), html, re.S)
    assert m, f"{begin} markers missing from index.html"
    return m.group(1)


def core_js():
    return _extract("/* LOCAL-CORE-BEGIN */", "/* LOCAL-CORE-END */")


def backend_js():
    return "let USER = '';\n" + _extract(
        "/* LOCAL-BACKEND-BEGIN */", "/* LOCAL-BACKEND-END */")


def run_node(script):
    out = subprocess.run([node, "-e", script], capture_output=True,
                         text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_content_key_parity(tmp_path):
    script = core_js() + f"""
const cases = {json.dumps(CASES, ensure_ascii=False)};
console.log(JSON.stringify(cases.map(pyKey)));
"""
    got = run_node(script)
    want = [content_key(c) for c in CASES]
    assert got == want


def test_locate_parity():
    script = core_js() + f"""
const text = {json.dumps(TEXT, ensure_ascii=False)};
const cases = {json.dumps(LOCATE_CASES, ensure_ascii=False)};
console.log(JSON.stringify(cases.map(([s, ctx]) => jsLocate(text, s, ctx))));
"""
    got = run_node(script)
    want = [locate(TEXT, s, ctx).__dict__ for s, ctx in LOCATE_CASES]
    assert got == want


def test_full_workflow_parity(basic_review):
    """Drive the same review (accept two same-line edits, apply, revert
    one) through the Python Store and the in-browser engine; the
    resulting decisions and manuscript text must match exactly."""
    from texreview.core import Store, apply_accepted

    pass_doc = json.loads(
        (basic_review / "review" / "0001-pass.json").read_text())
    paper = (basic_review / "paper.tex").read_text()

    # Python side
    st = Store(basic_review / "review")
    for sid in ("s2", "s3"):
        st.suggestion(sid)["status"] = "accepted"
    st.save()
    apply_accepted(st)
    st = Store(basic_review / "review")
    assert st.revert("s2") is True
    st = Store(basic_review / "review")
    st.purge()                            # drops the applied s3
    py_decisions = json.loads(
        (basic_review / "review" / "decisions.json").read_text())
    py_text = (basic_review / "paper.tex").read_text()
    py_pass = json.loads(
        (basic_review / "review" / "0001-pass.json").read_text())

    # JS side, same scenario
    script = backend_js() + f"""
(async () => {{
  LS.files.set("paper.tex", {json.dumps(paper, ensure_ascii=False)});
  LS.passes.push({{ name: "0001-pass.json",
                    doc: {json.dumps(pass_doc, ensure_ascii=False)} }});
  await localApi("/api/decide", {{ id: "s2", status: "accepted" }});
  await localApi("/api/decide", {{ id: "s3", status: "accepted" }});
  const rep = (await localApi("/api/apply", {{}})).data;
  const rev = await localApi("/api/revert", {{ id: "s2" }});
  const prune = (await localApi("/api/purge", {{}})).data;
  console.log(JSON.stringify({{
    rep, revOk: rev.ok, prune,
    decisions: LS.decisions,
    text: LS.files.get("paper.tex"),
    passIds: LS.passes[0].doc.suggestions.map(s => s.id),
  }}));
}})();
"""
    got = run_node(script)
    assert sorted(got["rep"]["applied"]) == ["s2", "s3"]
    assert got["rep"]["skipped"] == []
    assert got["revOk"] is True
    assert got["prune"]["removed"] == ["s3"]
    assert got["decisions"] == py_decisions
    assert got["text"] == py_text
    assert got["passIds"] == [s["id"] for s in py_pass["suggestions"]]
