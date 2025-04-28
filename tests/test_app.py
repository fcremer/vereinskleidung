"""End-to-End-Tests für die Flask-App (ohne externe Services)."""

from __future__ import annotations

from pathlib import Path

import yaml
from flask.testing import FlaskClient
from werkzeug.datastructures import MultiDict


def _client(test_app) -> FlaskClient:  # noqa: D401  (helper)
    """Kurz-Alias für den Test-Client."""
    return test_app.app.test_client()


# ---------------------------------------------------------------------------#
#  Index-Route                                                                #
# ---------------------------------------------------------------------------#
def test_get_index_page(test_app):
    resp = _client(test_app).get("/")
    assert resp.status_code == 200
    assert b"Vereinskleidung" in resp.data


def test_post_valid_order_creates_yaml(test_app, tmp_path):
    form = MultiDict(
        {
            "name": "Max Test",
            "qty_Poloshirt": "2",
            "size_Poloshirt": "M",
            "color_Poloshirt": "weiß",
            "pay_Poloshirt": "self",
        },
    )
    resp = _client(test_app).post("/", data=form, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Danke" in resp.data  # Flash-Meldung

    # YAML-Aggregat prüfen
    yml_file: Path = test_app.orders_dir / "pending.yml"
    data = yaml.safe_load(yml_file.read_text(encoding="utf-8"))
    assert data["Selbstzahler"]["Poloshirt"]["M"]["weiß"]["Max Test"] == 2


def test_post_without_articles_warns(test_app):
    resp = _client(test_app).post("/", data={"name": "Ohne Artikel"}, follow_redirects=True)
    assert b"mindestens einen Artikel" in resp.data


# ---------------------------------------------------------------------------#
#  Übersicht-Route                                                            #
# ---------------------------------------------------------------------------#
def test_overview_contains_order(test_app):
    """Nach der Bestellung erscheint sie in /uebersicht als Tabellenzeile."""
    # 1 Bestellung erzeugen
    _client(test_app).post(
        "/",
        data={
            "name": "Lisa Käufer",
            "qty_Poloshirt": "1",
            "size_Poloshirt": "L",
            "color_Poloshirt": "rot",
            "pay_Poloshirt": "club",
        },
        follow_redirects=True,
    )

    # Übersicht abrufen
    resp = _client(test_app).get("/uebersicht")
    assert resp.status_code == 200
    # Tabellen-Snippet prüfen
    assert b"Vereinskosten" in resp.data
    assert b"Poloshirt" in resp.data
    assert b"L" in resp.data
    assert b"rot" in resp.data
    assert b"Lisa K"[:8] in resp.data  # Namensanfang reicht