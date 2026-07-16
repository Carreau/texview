"""tex-review: accept/reject agent-suggested edits before applying them.

An agent emits suggested edits to a LaTeX manuscript as JSON pass files
(it never edits the .tex files); a human accepts/rejects them one by
one in a local web UI; the tool applies only the accepted ones. Edits
are anchored on exact text, not line numbers, so several independent
changes on the same line never collide.

Stdlib only. Python 3.9+.
"""

__version__ = "0.2.0"
