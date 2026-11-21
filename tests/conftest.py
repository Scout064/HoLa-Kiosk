"""
Kiosk test setup.

The kiosk modules run INSIDE the HoLa-Dash backend, so the tests reuse the
dash backend's environment: run them with the dash venv from either repo:

    cd HoLa_Kiosk && <dash-venv>/bin/python -m pytest
    cd HoLa_Dash/backend && uv run python -m pytest ../../HoLa_Kiosk

(HOLA_DASH_DIR can override the assumed sibling location.)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

KIOSK_ROOT = Path(__file__).resolve().parents[1]
MODULES_ROOT = KIOSK_ROOT / "modules"


def _dash_backend() -> Path:
    env = os.environ.get("HOLA_DASH_DIR")
    if env:
        return Path(env).resolve()
    return (KIOSK_ROOT.parent / "HoLa_Dash" / "backend").resolve()


DASH_BACKEND = _dash_backend()

for path in (str(DASH_BACKEND), str(MODULES_ROOT)):
    if path not in sys.path:
        sys.path.append(path)

import pytest

from app.core import crypto


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """Keep the Fernet key out of the real repo for every test."""
    monkeypatch.setenv("HOLA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("HOLA_CONFIG", raising=False)
    crypto._fernet.cache_clear()
    yield
    crypto._fernet.cache_clear()
