#!/usr/bin/env python3
"""Compatibility shim: run the texreview package from a checkout.

    python review.py serve example/review   ==  tex-review review example/review

Prefer installing the package (`pipx install .` or `uvx tex-review`)
and using the `tex-review` command; see README.md.
"""

import sys

from texreview.cli import main

if __name__ == "__main__":
    sys.exit(main())
