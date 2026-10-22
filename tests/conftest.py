"""Глобальне налаштування pytest: виправляє взаємодію six._SixMetaPathImporter (pynput) з PySide6 shiboken на Python 3.12."""

from __future__ import annotations

import six

if hasattr(six, "_SixMetaPathImporter") and not hasattr(six._SixMetaPathImporter, "_path"):
    six._SixMetaPathImporter._path = []  # type: ignore[attr-defined]
