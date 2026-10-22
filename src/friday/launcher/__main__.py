"""Точка входу: python -m friday.launcher."""

from __future__ import annotations

import sys

from friday.launcher.app import run_launcher


if __name__ == "__main__":
    raise SystemExit(run_launcher(sys.argv[1:]))
