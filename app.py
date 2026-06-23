"""
Listing Copilot — MVP backend
A small Flask app that turns raw product info into SEO-optimized,
conversion-focused marketplace copy using the Claude API.

Run locally:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=sk-ant-...
    python app.py
Then open http://localhost:5000
"""

import os
import io
import csv
import json
import time
import hmac
import base64
import hashlib

from datetime import datetime, timezone, timedelta

from flask import Flask, request, jsonify, send_from_directory, Response
from anthropic import Anthropic

app = Flask(__name__, static_folder="static", static_url_path="")

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ---------------------------------------------------------------------------
# Free trial + paid unlock
#
# Why this exists: the original MVP had zero connection between "use the
# product" and "pay for the product" — anyone could generate unlimited
# listings for free, and the only way to "activate" a paid plan was a manual
# 24-hour email from the founder. Neither builds trust with a stranger
# comparing this to an established, reviewed competitor.
#
# This section does two things instead:
#   1. Lets anyone try a few real listings with zero signup and no card —
#      the proven pattern for getting a stranger to trust an unknown brand
#      (prove the output quality before asking for money).
#   2. Lets a paying customer unlock full access instantly by entering the
#      email they paid with — checked live against Stripe, no waiting on a
#      manual email, no password to manage.
# ---------------------------------------------------------------------------

FREE_TRIAL_LIMIT = int(os.environ.get("FREE_TRIAL_LIMIT", "3"))
TRIAL_WINDOW_HOURS = 24
TRIAL_STORE_PATH = os.environ.get("TRIAL_STORE_PATH", "trial_usage.json")
UNLOCK_SECRET = os.environ.get("UNLOCK_SECRET", "dev-secret-change-me-in-production")
UNLOCK_COOKIE = "lc_pro"
UNLOCK_DAYS = 30

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
try:
    import stripe as _stripe_sdk

    if STRIPE_SECRET_KEY:
        _stripe_sdk.api_key = STRIPE_SECRET_KEY
    else:
        _stripe_sdk = None
except ImportError:
    _stripe_sdk = None


def _load_trial_store() -> dict:
    try:
        with open(TRIAL_STORE_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_trial_store(store: dict) -> None:
    try:
        with open(TRIAL_STORE_PATH, "w") as f:
            json.dump(store, f)
    except OSError:
        pass  # best-effort cache; never block a request on disk issues


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _window_expired(window_start_iso: str) -> bool:
    started = datetime.fromisoformat(window_start_iso)
    return datetime.now(timezone.utc) - started > timedelta(hours=TRIAL_WINDOW_HOURS)


def trial_status(ip: str):
    """Returns (remaining, limit) for this IP without consuming anything."""
    store = _load_trial_store()
    entry = store.get(ip)
    if not entry or _window_expired(entry["window_start"]):
        return FREE_TRIAL_LIMIT, FREE_TRIAL_LIMIT
    return max(0, FREE_TRIAL_LIMIT - entry["count"]), FREE_TRIAL_LIMIT


def consume_trial(ip: str, n: int = 1) -> int:
    """Consumes n free generations for this IP. Returns remaining count."""
    store = _load_trial_store()
    entry = store.get(ip)
    if not entry or _window_expired(entry["window_start"]):
        entry = {"window_start": datetime.now(timezone.utc).isoformat(), "count": 0}
    entry["count"] += n
    store[ip] = entry
    _save_trial_store(store)
    return max(0, FREE_TRIAL_LIMIT - entry["count"])


def make_unlock_token(email: str) -> str:
    payload = f"{email.lower()}|{int(time.time())}"
    sig = hmac.new(UNLOCK_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    raw = f"{payload}|{sig}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def verify_unlock_token(token: str):
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        email, ts, sig = raw.split("|")
        expected = hmac.new(
            UNLOCK_SECRET.encode(), f"{email}|{ts}".encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if time.time() - float(ts) > UNLOCK_DAYS * 86400:
            return None
        return email
    except Exception:
        return None


def is_unlocked() -> bool:
    token = request.cookies.get(UNLOCK_COOKIE)
    return bool(token and verify_unlock_token(token))


def stripe_email_has_paid(email: str) -> bool:
    """Checks Stripe directly for an active subscription or a successful,
    non-refunded charge tied to this email. Fails closed (False) if Stripe
    isn't configured or the lookup errors — never fails open."""
    if not _stripe_sdk or not STRIPE_SECRET_KEY:
        return False
    try:
        customers = _stripe_sdk.Customer.list(email=email, limit=10)
        for cust in customers.auto_paging_iter():
            subs = _stripe_sdk.Subscription.list(customer=cust.id, status="active", limit=5)
            if subs.data:
                return True
            charges = _stripe_sdk.Charge.list(customer=cust.id, limit=10)
            if any(c.get("paid") and not c.get("refunded") for c in charges.data):
                return True
        return False
    except Exception:
        return False


SYSTEM_PROMPT = """You are an expert e-commerce copywriter who specializes in Etsy, \
Shopify, and Amazon listings for independent and handmade sellers. You write copy that \
sounds like a skilled human copywriter, not generic AI output. You follow each \
platform's SEO rules precisely and you always write in the seller's specified brand \
voice/tone.

Etsy rules you must follow:
- Title: max 140 characters, front-load the single most important buyer search term, \
no keyword stuffing, no ALL CAPS.
- Tags: exactly 13 tags, each max 20 characters, all lowercase, no duplicate words \
across tags, mix of broad and long-tail buyer search phrases (not just product nouns).
- Description: open with a 1-2 sentence hook that speaks to the buyer's desire or \
problem, then a short story/craftsmanship paragraph, then a clear bullet list of \
specs/materials/dimensions, then care instructions, then shipping/processing note, \
then a soft call-to-action.

Always return ONLY valid JSON matching this exact schema, no markdown fences, no \
commentary:
{
  "title": "string",
  "tags": ["string", ... exactly 13 items for etsy, omit/empty for other platforms],
  "description": "string with \\n for line breaks",
  "pinterest_description": "string, <= 500 characters, includes 2-3 relevant hashtags",
  "instagram_caption": "string, <= 2200 characters, includes a hook line, 3-5 relevant \
hashtags, and a soft CTA"
}
"""


def build_user_prompt(product: dict) -> str:
    return f"""Generate marketplace listing copy for this product.

Platform: {product.get('platform', 'etsy')}
Product name: {product.get('product_name', '')}
Category: {product.get('category', '')}
Materials / ingredients: {product.get('materials', '')}
Key features / what makes it special: {product.get('features', '')}
Target buyer: {product.get('audience', '')}
Brand voice / tone: {product.get('tone', 'warm, friendly, a little playful')}
Price (optional, for context only): {product.get('price', '')}

Return the JSON object now."""


def generate_listing(product: dict) -> dict:
    message = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_prompt(product)}],
    )
    raw_text = "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()

    # Strip accidental markdown code fences if the model adds them.
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.lower().startswith("json"):
            raw_text = raw_text[4:].strip()

    return json.loads(raw_text)


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/trial-status")
def api_trial_status():
    if is_unlocked():
        return jsonify({"unlocked": True})
    remaining, limit = trial_status(_client_ip())
    return jsonify({"unlocked": False, "remaining": remaining, "limit": limit})


@app.route("/api/unlock", methods=["POST"])
def api_unlock():
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip()
    if not email or "@" not in email:
        return jsonify({"ok": False, "error": "Enter the email you paid with."}), 400

    if not stripe_email_has_paid(email):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": (
                        "We couldn't find an active paid plan for that email yet. "
                        "Just paid? Give it a minute and try again, or reply to your "
                        "Stripe receipt email and it'll reach the founder directly."
                    ),
                }
            ),
            402,
        )

    token = make_unlock_token(email)
    resp = jsonify({"ok": True})
    resp.set_cookie(
        UNLOCK_COOKIE,
        token,
        max_age=UNLOCK_DAYS * 86400,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure,
    )
    return resp


@app.route("/api/generate", methods=["POST"])
def api_generate():
    product = request.get_json(force=True)
    unlocked = is_unlocked()
    ip = _client_ip()

    if not unlocked:
        remaining, limit = trial_status(ip)
        if remaining <= 0:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "free_trial_exhausted",
                        "message": (
                            f"You've used all {limit} free listings for today. "
                            "Pick a plan below to keep going, or come back tomorrow "
                            "for more free tries."
                        ),
                    }
                ),
                402,
            )

    try:
        result = generate_listing(product)
    except Exception as exc:  # noqa: BLE001 - surface error to the UI
        return jsonify({"ok": False, "error": str(exc)}), 500

    remaining = None
    if not unlocked:
        remaining = consume_trial(ip)

    return jsonify(
        {"ok": True, "result": result, "unlocked": unlocked, "trial_remaining": remaining}
    )


@app.route("/api/bulk", methods=["POST"])
def api_bulk():
    """Accepts a CSV upload with columns:
    platform,product_name,category,materials,features,audience,tone,price
    Returns a CSV with the generated copy appended.

    Free-trial visitors get up to their remaining free-listing allowance
    processed as a preview; the rest are flagged as needing an unlock so
    they can see the tool work on their own real catalog before paying for
    the rest of it.
    """
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    unlocked = is_unlocked()
    ip = _client_ip()
    free_budget = None if unlocked else trial_status(ip)[0]

    text = file.stream.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows_out = []
    errors = []
    processed = 0

    for i, row in enumerate(reader):
        if free_budget is not None and processed >= free_budget:
            errors.append(
                f"Row {i + 1} ({row.get('product_name', '?')}): skipped — free trial "
                "limit reached for today. Unlock full access to process your whole catalog."
            )
            continue
        try:
            result = generate_listing(row)
            rows_out.append(
                {
                    "product_name": row.get("product_name", ""),
                    "title": result.get("title", ""),
                    "tags": ", ".join(result.get("tags", [])),
                    "description": result.get("description", ""),
                    "pinterest_description": result.get("pinterest_description", ""),
                    "instagram_caption": result.get("instagram_caption", ""),
                }
            )
            processed += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Row {i + 1} ({row.get('product_name', '?')}): {exc}")
        time.sleep(0.2)  # gentle pacing for rate limits

    if not unlocked and processed:
        consume_trial(ip, processed)

    output = io.StringIO()
    if rows_out:
        writer = csv.DictWriter(output, fieldnames=list(rows_out[0].keys()))
        writer.writeheader()
        writer.writerows(rows_out)

    resp = Response(output.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = "attachment; filename=listings_output.csv"
    if errors:
        resp.headers["X-Generation-Errors"] = str(len(errors))
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
