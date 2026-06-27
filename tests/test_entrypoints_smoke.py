"""Smoke test: los entrypoints importan limpio y exponen main().

No ejecuta ``main()`` (cablea adaptadores reales de red), pero garantizar que
los módulos cargan sin errores de import protege el deploy.
"""

from __future__ import annotations

import importlib


def test_entrypoints_importan_y_tienen_main() -> None:
    for mod_name in (
        "cxc.run_scraper",
        "cxc.run_sync",
        "cxc.run_engine",
        "cxc.run_reconcile",
    ):
        mod = importlib.import_module(mod_name)
        assert callable(mod.main)
