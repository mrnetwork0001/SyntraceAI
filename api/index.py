"""Vercel entrypoint - read-only public instance.

Vercel runs each request in a fresh, filesystem-read-only function, so the
campaign endpoints could not work here even if they were allowed: they spawn a
subprocess and stream its output across many polls. Public mode is forced on
in code rather than left to a dashboard environment variable, because a
forgotten variable would be the one mistake that exposes /api/run and
/api/reset on a public URL.
"""

import os
import sys
from pathlib import Path

os.environ["SYNTRACE_PUBLIC"] = "1"

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dashboard.server import app  # noqa: E402  (after the path and flag are set)

__all__ = ["app"]
