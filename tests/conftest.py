from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Generator

import pytest


@pytest.fixture()
def test_app(tmp_path, monkeypatch) -> Generator:
    """Flask-App mit temporären Pfaden & stummen Neben­effekten."""
    # Temp-Verzeichnisse
    config_dir = tmp_path / "config"
    orders_dir = tmp_path / "orders"
    config_dir.mkdir()
    orders_dir.mkdir()

    # Mini-Configs
    (config_dir / "config.yml").write_text(
        "admin_email: test@example.org\nsmtp: {enabled: false}\nrecaptcha: {enabled: false}\n"
    )
    (config_dir / "items.yml").write_text(
        "Poloshirt:\n  default_colors: [weiß, rot]\n"
    )

    # Projekt-Root in sys.path
    root_dir = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root_dir))

    # App-Modul laden (Untermodul!)
    app_module = importlib.import_module("app.app")

    # Konstante Pfade patchen
    monkeypatch.setattr(app_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(app_module, "ORDERS_DIR", orders_dir)
    monkeypatch.setattr(app_module, "CONFIG_FILE", config_dir / "config.yml")
    monkeypatch.setattr(app_module, "ITEMS_FILE", config_dir / "items.yml")

    # Neben­effekte ausschalten
    monkeypatch.setattr(app_module, "send_mail", lambda *_a, **_k: None)
    monkeypatch.setattr(app_module, "send_pushover", lambda *_a, **_k: None)

    yield SimpleNamespace(app=app_module.app, orders_dir=orders_dir)