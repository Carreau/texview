"""Command-line interface.

    tex-review review   [review-dir | suggestions.json] [--port N] [--open]
    tex-review apply    [review-dir | suggestions.json]
    tex-review check    [review-dir | suggestions.json]
    tex-review instruct [--schema]
"""

from __future__ import annotations

import argparse
import sys
from importlib.resources import files
from pathlib import Path

from .core import Store, annotate, apply_accepted
from .server import serve


def _add_target(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "target", nargs="?", default="review",
        help="review/ directory of agent passes, or a single "
             "suggestions.json (default: ./review)",
    )


def _load_store(target: str) -> Store | None:
    path = Path(target)
    if not (path.is_file() or path.is_dir()):
        print(f"error: {path} not found", file=sys.stderr)
        return None
    return Store(path)


def cmd_review(args) -> int:
    store = _load_store(args.target)
    if store is None:
        return 1
    serve(store, port=args.port, open_browser=args.open)
    return 0


def cmd_check(args) -> int:
    store = _load_store(args.target)
    if store is None:
        return 1
    bad = 0
    for r in annotate(store):
        state = r["match"]["state"]
        flag = "ok " if state == "ok" else "!! "
        bad += state != "ok" and r["status"] != "applied"
        print(f"{flag}{r['id']:>14}  {r['status']:<9} "
              f"{state:<10} {r['file']}:{r['match']['line']}")
    return 1 if bad else 0


def cmd_apply(args) -> int:
    store = _load_store(args.target)
    if store is None:
        return 1
    report = apply_accepted(store)
    print(f"applied {len(report['applied'])}: "
          f"{', '.join(report['applied']) or '-'}")
    for sk in report["skipped"]:
        print(f"skipped {sk['id']}: {sk['reason']}")
    return 0


def cmd_instruct(args) -> int:
    pkg = files("texreview")
    schema = pkg.joinpath("schema.json").read_text(encoding="utf-8")
    if args.schema:
        print(schema, end="")
        return 0
    print(pkg.joinpath("instructions.md").read_text(encoding="utf-8"))
    print("## JSON Schema (machine-readable)\n")
    print("```json")
    print(schema, end="")
    print("```")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="tex-review",
        description="Review agent-suggested edits to a LaTeX manuscript, "
                    "then apply only the accepted ones.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("review", aliases=["serve"],
                       help="start the review server (web UI)")
    _add_target(p)
    p.add_argument("--port", type=int, default=8123)
    p.add_argument("--open", action="store_true",
                   help="open the UI in a browser")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("apply", help="apply accepted suggestions")
    _add_target(p)
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("check", help="validate that all anchors resolve")
    _add_target(p)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser(
        "instruct",
        help="print the instructions + JSON schema to give a "
             "suggesting agent",
    )
    p.add_argument("--schema", action="store_true",
                   help="print only the machine-readable JSON schema")
    p.set_defaults(func=cmd_instruct)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
