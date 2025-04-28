"""
Flask-App für Vereins­kleidung – PEP 8-konform (flake8 OK)
* Bestell-Formular      (/)
* Übersicht aller Orders (/uebersicht)
* YAML-Aggregation (payment → item → size → color → buyer)
* Mail- und Pushover-Benachrichtigung
"""

from __future__ import annotations

import datetime
import os
import ssl
import uuid
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import requests
import smtplib
import yaml
from flask import Flask, flash, redirect, render_template, request

# ---------------------------------------------------------------------------#
#  Pfade & Konstanten                                                        #
# ---------------------------------------------------------------------------#
BASE_DIR = Path(__file__).parent
ROOT_DIR = BASE_DIR.parent
CONFIG_DIR = ROOT_DIR / "config"
ORDERS_DIR = ROOT_DIR / "orders"

CONFIG_FILE = CONFIG_DIR / "config.yml"
ITEMS_FILE = CONFIG_DIR / "items.yml"

SIZES = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "3XL"]
PAY_OPTS = {"self": "Selbstzahler", "club": "Vereinskosten"}

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(24))


# ---------------------------------------------------------------------------#
#  YAML-Helfer                                                               #
# ---------------------------------------------------------------------------#
def load_yaml(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def save_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True))


# ---------------------------------------------------------------------------#
#  Aggregation (payment → item → size → color → buyer)                       #
# ---------------------------------------------------------------------------#
def aggregate_order(order: dict[str, Any], filename: str = "pending.yml") -> None:
    file_path = ORDERS_DIR / filename
    root: dict[str, Any] = load_yaml(file_path)

    for art in order["articles"]:
        payment = PAY_OPTS.get(art["payment"], art["payment"])
        item = art["item"]
        size = art.get("size") or "–"
        color = art.get("color") or "Standard"
        buyer = order["buyer"]
        qty = art["qty"]

        root.setdefault(payment, {}) \
            .setdefault(item, {}) \
            .setdefault(size, {}) \
            .setdefault(color, {}) \
            .setdefault(buyer, 0)

        root[payment][item][size][color][buyer] += qty

    save_yaml(file_path, root)


# ---------------------------------------------------------------------------#
#  Mail-Versand                                                              #
# ---------------------------------------------------------------------------#
def send_mail(subject: str, body: str) -> None:
    cfg = load_yaml(CONFIG_FILE)
    smtp_cfg = cfg.get("smtp", {})
    if not smtp_cfg or not smtp_cfg.get("enabled", True):
        return

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_cfg["user"]
    message["To"] = cfg["admin_email"]
    message.set_content(body)

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(smtp_cfg["host"], smtp_cfg["port"]) as smtp:
            smtp.starttls(context=context)
            smtp.login(
                smtp_cfg["user"],
                os.environ.get("SMTP_PASSWORD", smtp_cfg["password"]),
            )
            smtp.send_message(message)
    except Exception as exc:  # pragma: no cover
        app.logger.error("E-Mail-Versand fehlgeschlagen: %s", exc)


# ---------------------------------------------------------------------------#
#  Pushover-Push                                                             #
# ---------------------------------------------------------------------------#
def send_pushover(buyer: str) -> None:
    pcfg = load_yaml(CONFIG_FILE).get("pushover", {})
    if not pcfg or not pcfg.get("enabled", True):
        return

    payload = {
        "token": os.environ.get("PUSHOVER_TOKEN") or pcfg["token"],
        "user": os.environ.get("PUSHOVER_USER") or pcfg["user_key"],
        "title": "Neue Vereinsbestellung",
        "message": f"{buyer} hat soeben eine Bestellung abgegeben.",
        "priority": 0,
    }
    if pcfg.get("device"):
        payload["device"] = pcfg["device"]

    try:
        resp = requests.post(
            "https://api.pushover.net/1/messages.json",
            data=payload,
            timeout=5,
            verify=pcfg.get("verify_ssl", True),
        )
        if resp.status_code != 200:  # pragma: no cover
            app.logger.error("Pushover-Fehler %s: %s", resp.status_code, resp.text)
    except requests.RequestException as exc:  # pragma: no cover
        app.logger.error("Pushover-Request fehlgeschlagen: %s", exc)


# ---------------------------------------------------------------------------#
#  CAPTCHA-Check                                                             #
# ---------------------------------------------------------------------------#
def recaptcha_ok(token: str | None, ip: str) -> bool:
    cfg = load_yaml(CONFIG_FILE)
    rcfg = cfg.get("recaptcha", {})
    if not rcfg.get("enabled", True):
        return True

    secret = os.environ.get("RECAPTCHA_SECRET_KEY") or rcfg.get("secret_key")
    if not secret:
        return True

    try:
        resp = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": secret, "response": token, "remoteip": ip},
            timeout=5,
        )
        return bool(resp.json().get("success"))
    except requests.RequestException:
        return False


# ---------------------------------------------------------------------------#
#  Hilfsfunktion: YAML → flache Zeilen (für /uebersicht)                     #
# ---------------------------------------------------------------------------#
def flatten_orders(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pay, items in data.items():
        for item, sizes in items.items():
            for size, colors in sizes.items():
                for color, buyers in colors.items():
                    for buyer, qty in buyers.items():
                        rows.append(
                            {
                                "payment": pay,
                                "item": item,
                                "size": size,
                                "color": color,
                                "buyer": buyer,
                                "qty": qty,
                            },
                        )
    rows.sort(key=lambda r: (r["payment"], r["item"], r["size"], r["color"], r["buyer"]))
    return rows


# ---------------------------------------------------------------------------#
#  Routes                                                                    #
# ---------------------------------------------------------------------------#
@app.route("/", methods=["GET", "POST"])
def index() -> str:  # noqa: C901  (complexity OK für scope)
    items = load_yaml(ITEMS_FILE)
    cfg = load_yaml(CONFIG_FILE)
    rcfg = cfg.get("recaptcha", {})

    captcha_on = rcfg.get("enabled", True) and bool(
        os.environ.get("RECAPTCHA_SITE_KEY") or rcfg.get("site_key"),
    )

    if request.method == "POST":
        if not recaptcha_ok(request.form.get("g-recaptcha-response"), request.remote_addr):
            flash("CAPTCHA fehlgeschlagen – bitte erneut versuchen.", "danger")
            return redirect(request.url)

        order: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "timestamp": (
                datetime.datetime.now(datetime.timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            ),
            "buyer": request.form["name"],
            "articles": [],
        }

        # Standardartikel
        for item_name in items.keys():
            qty = int(request.form.get(f"qty_{item_name}", "0"))
            if qty:
                order["articles"].append(
                    {
                        "item": item_name,
                        "qty": qty,
                        "color": request.form.get(f"color_{item_name}") or "Standard",
                        "size": request.form.get(f"size_{item_name}") or "–",
                        "payment": request.form.get(f"pay_{item_name}", "self"),
                    },
                )

        # Individuelle Artikel
        idx = 0
        while f"c_item_{idx}" in request.form:
            order["articles"].append(
                {
                    "item": request.form[f"c_item_{idx}"],
                    "qty": int(request.form[f"c_qty_{idx}"]),
                    "color": request.form[f"c_color_{idx}"],
                    "size": request.form[f"c_size_{idx}"],
                    "payment": request.form[f"c_pay_{idx}"],
                    "custom": True,
                },
            )
            idx += 1

        if not order["articles"]:
            flash("Bitte mindestens einen Artikel auswählen.", "warning")
            return redirect(request.url)

        aggregate_order(order)
        send_mail("Neue Vereinsbestellung", yaml.safe_dump(order, allow_unicode=True))
        send_pushover(order["buyer"])
        flash("Danke – deine Bestellung wurde aufgenommen!", "success")
        return redirect(request.url)

    site_key = os.environ.get("RECAPTCHA_SITE_KEY") or rcfg.get("site_key", "")
    return render_template(
        "index.html",
        items=items,
        SIZES=SIZES,
        PAY_OPTS=PAY_OPTS,
        captcha_on=captcha_on,
        site_key=site_key,
    )


@app.route("/uebersicht", methods=["GET"])
def overview() -> str:
    """Zeigt eine Tabelle aller bislang aggregierten Bestellungen."""
    orders_yml: dict[str, Any] = load_yaml(ORDERS_DIR / "pending.yml")
    rows = flatten_orders(orders_yml)
    return render_template("overview.html", rows=rows)


# ---------------------------------------------------------------------------#
#  Debug-Start                                                               #
# ---------------------------------------------------------------------------#
if __name__ == "__main__":  # pragma: no cover
    app.run(host="0.0.0.0", port=8000, debug=False)
