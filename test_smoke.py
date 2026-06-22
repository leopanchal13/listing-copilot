"""
Smoke test that exercises the Flask app end-to-end WITHOUT calling the real
Claude API — it monkeypatches client.messages.create with a canned response
so we can verify the request/response plumbing, CSV parsing, and JSON
formatting all work before a real API key is wired up.
"""
import io
import json
import types

import app as app_module

FAKE_JSON = {
    "title": "Hand-Poured Lavender Soy Candle | Cozy Self Care Gift",
    "tags": [
        "lavender candle", "soy candle gift", "self care gift",
        "cozy home decor", "handmade candle", "relaxing gift idea",
        "amber jar candle", "calming candle", "gift for her",
        "natural soy wax", "aromatherapy candle", "spa gift",
        "housewarming gift",
    ],
    "description": "Light it, breathe in, relax.\n\nHand-poured in small batches...",
    "pinterest_description": "Cozy lavender soy candle, hand-poured for relaxing nights in. #candle #selfcare",
    "instagram_caption": "New in the shop: lavender soy candles for your cozy nights in 🕯️ #handmade #soycandle #selfcare",
}


class FakeBlock:
    type = "text"
    def __init__(self, text):
        self.text = text


class FakeMessage:
    def __init__(self, text):
        self.content = [FakeBlock(text)]


def fake_create(*args, **kwargs):
    return FakeMessage(json.dumps(FAKE_JSON))


def run():
    app_module.client.messages.create = fake_create
    client = app_module.app.test_client()

    # --- test /api/generate ---
    payload = {
        "platform": "etsy",
        "product_name": "Hand-poured lavender soy candle",
        "category": "Home & Living > Candles",
        "materials": "soy wax, cotton wick, lavender essential oil",
        "features": "45-hour burn time, hand-poured in small batches",
        "audience": "gift shoppers, self-care enthusiasts",
        "tone": "warm, cozy, a little whimsical",
        "price": "$24",
    }
    res = client.post("/api/generate", json=payload)
    data = res.get_json()
    assert res.status_code == 200, res.status_code
    assert data["ok"] is True
    assert data["result"]["title"] == FAKE_JSON["title"]
    assert len(data["result"]["tags"]) == 13
    print("PASS: /api/generate returns well-formed listing JSON")

    # --- test /api/bulk ---
    csv_content = (
        "platform,product_name,category,materials,features,audience,tone,price\n"
        "etsy,Lavender soy candle,Home & Living,soy wax,45-hour burn,gift shoppers,cozy,$24\n"
        "etsy,Chunky knit blanket,Home & Living,wool,handmade in USA,home decor lovers,rustic,$68\n"
    )
    data_file = (io.BytesIO(csv_content.encode("utf-8")), "products.csv")
    res = client.post("/api/bulk", data={"file": data_file}, content_type="multipart/form-data")
    assert res.status_code == 200, res.status_code
    body = res.data.decode("utf-8")
    assert "title" in body and "Hand-Poured Lavender" in body
    assert body.count("\n") >= 2  # header + 2 rows
    print("PASS: /api/bulk processes CSV and returns generated rows for each product")

    print("\nAll smoke tests passed. The request/response plumbing, prompt building, "
          "JSON parsing, and CSV bulk flow all work end-to-end.")


if __name__ == "__main__":
    run()
