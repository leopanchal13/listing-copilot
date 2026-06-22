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

from flask import Flask, request, jsonify, send_from_directory, Response
from anthropic import Anthropic

app = Flask(__name__, static_folder="static", static_url_path="")

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

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


@app.route("/api/generate", methods=["POST"])
def api_generate():
    product = request.get_json(force=True)
    try:
        result = generate_listing(product)
        return jsonify({"ok": True, "result": result})
    except Exception as exc:  # noqa: BLE001 - surface error to the UI
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/bulk", methods=["POST"])
def api_bulk():
    """Accepts a CSV upload with columns:
    platform,product_name,category,materials,features,audience,tone,price
    Returns a CSV with the generated copy appended.
    """
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    text = file.stream.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows_out = []
    errors = []

    for i, row in enumerate(reader):
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
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Row {i + 1} ({row.get('product_name', '?')}): {exc}")
        time.sleep(0.2)  # gentle pacing for rate limits

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
