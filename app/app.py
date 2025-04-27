import os, uuid, yaml, datetime, smtplib, ssl, requests
from pathlib import Path
from email.message import EmailMessage
from flask import Flask, render_template, request, redirect, flash

# ------------------------------------------------------------
# Pfade & Konstanten
# ------------------------------------------------------------
BASE_DIR    = Path(__file__).parent
ROOT_DIR    = BASE_DIR.parent
CONFIG_DIR  = ROOT_DIR / "config"        # <— NEU
ORDERS_DIR  = ROOT_DIR / "orders"

CONFIG_FILE = CONFIG_DIR / "config.yml"  # <— Pfade angepasst
ITEMS_FILE  = CONFIG_DIR / "items.yml"

SIZES    = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "3XL"]
PAY_OPTS = {"self": "Selbstzahler", "club": "Vereinskosten"}

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(24))

# ------------------------------------------------------------
# YAML-Helfer
# ------------------------------------------------------------
def load_yaml(path: Path):
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def save_yaml(path: Path, data):
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True))

# ------------------------------------------------------------
# Bestellung in verschachtelter Struktur speichern
# ------------------------------------------------------------
def aggregate_order(order, filename: str = "pending.yml"):
    file = ORDERS_DIR / filename
    root = load_yaml(file)

    for art in order["articles"]:
        pay   = PAY_OPTS.get(art["payment"], art["payment"])
        item  = art["item"]
        size  = art.get("size")  or "–"
        color = art.get("color") or "Standard"
        buyer = order["buyer"]
        qty   = art["qty"]

        root.setdefault(pay, {}) \
            .setdefault(item, {}) \
            .setdefault(size, {}) \
            .setdefault(color, {}) \
            .setdefault(buyer, 0)

        root[pay][item][size][color][buyer] += qty

    save_yaml(file, root)

# ------------------------------------------------------------
# Mail-Versand
# ------------------------------------------------------------
def send_mail(subject: str, body: str):
    cfg = load_yaml(CONFIG_FILE)
    smtp_cfg = cfg.get("smtp", {})
    if not smtp_cfg or not smtp_cfg.get("enabled", True):
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = smtp_cfg["user"]
    msg["To"]      = cfg["admin_email"]
    msg.set_content(body)

    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP(smtp_cfg["host"], smtp_cfg["port"]) as s:
            s.starttls(context=ctx)
            s.login(
                smtp_cfg["user"],
                os.environ.get("SMTP_PASSWORD", smtp_cfg["password"]),
            )
            s.send_message(msg)
    except Exception as e:
        app.logger.error("E-Mail-Versand fehlgeschlagen: %s", e)

# ------------------------------------------------------------
# Pushover-Push
# ------------------------------------------------------------
def send_pushover(buyer: str):
    pcfg = load_yaml(CONFIG_FILE).get("pushover", {})
    if not pcfg or not pcfg.get("enabled", True):
        return
    payload = {
        "token":   os.environ.get("PUSHOVER_TOKEN") or pcfg["token"],
        "user":    os.environ.get("PUSHOVER_USER")  or pcfg["user_key"],
        "title":   "Neue Vereinsbestellung",
        "message": f"{buyer} hat soeben eine Bestellung abgegeben.",
        "priority": 0,
    }
    if pcfg.get("device"):
        payload["device"] = pcfg["device"]

    try:
        r = requests.post(
            "https://api.pushover.net/1/messages.json",
            data=payload,
            timeout=5,
            verify=pcfg.get("verify_ssl", True),
        )
        if r.status_code != 200:
            app.logger.error("Pushover-Fehler %s: %s", r.status_code, r.text)
    except requests.RequestException as e:
        app.logger.error("Pushover-Request fehlgeschlagen: %s", e)

# ------------------------------------------------------------
# reCAPTCHA-Check
# ------------------------------------------------------------
def recaptcha_ok(token, ip) -> bool:
    cfg   = load_yaml(CONFIG_FILE)
    rcfg  = cfg.get("recaptcha", {})
    if not rcfg.get("enabled", True):
        return True
    secret = os.environ.get("RECAPTCHA_SECRET_KEY") or rcfg.get("secret_key")
    if not secret:
        return True
    try:
        r = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": secret, "response": token, "remoteip": ip},
            timeout=5,
        )
        return r.json().get("success", False)
    except requests.RequestException:
        return False

# ------------------------------------------------------------
# Routes
# ------------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    items = load_yaml(ITEMS_FILE)
    cfg   = load_yaml(CONFIG_FILE)
    rcfg  = cfg.get("recaptcha", {})
    captcha_on = rcfg.get("enabled", True) and bool(
        os.environ.get("RECAPTCHA_SITE_KEY") or rcfg.get("site_key")
    )

    if request.method == "POST":
        if not recaptcha_ok(request.form.get("g-recaptcha-response"),
                            request.remote_addr):
            flash("CAPTCHA fehlgeschlagen – bitte erneut versuchen.", "danger")
            return redirect(request.url)

        order = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "buyer": request.form["name"],
            "articles": [],
        }

        # ---------- Standardartikel -------------------------------
        for item in items.keys():
            qty = int(request.form.get(f"qty_{item}", "0"))
            if qty:
                order["articles"].append({
                    "item": item,
                    "qty": qty,
                    "color": request.form.get(f"color_{item}") or "Standard",
                    "size":  request.form.get(f"size_{item}")  or "–",
                    "payment": request.form.get(f"pay_{item}", "self"),
                })

        # ---------- Individuelle Artikel --------------------------
        i = 0
        while f"c_item_{i}" in request.form:
            order["articles"].append({
                "item": request.form[f"c_item_{i}"],
                "qty": int(request.form[f"c_qty_{i}"]),
                "color": request.form[f"c_color_{i}"],
                "size":  request.form[f"c_size_{i}"],
                "payment": request.form[f"c_pay_{i}"],
                "custom": True,
            })
            i += 1

        if not order["articles"]:
            flash("Bitte mindestens einen Artikel auswählen.", "warning")
            return redirect(request.url)

        # -------- Speichern & Benachrichtigen --------------------
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

# ------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001, debug=True)