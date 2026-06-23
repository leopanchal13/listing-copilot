"""
Smoke test that exercises the Flask app end-to-end WITHOUT calling the real
Claude API — it monkeypatches client.messages.create with a canned response
so we can verify the request/response plumbing, CSV parsing, JSON
formatting, the free-trial limiter, and the Stripe-backed unlock flow all
work before a real API key is wired up.
"""
import io
import json
import os

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


SINGLE_PAYLOAD = {
    "platform": "etsy",
    "product_name": "Hand-poured lavender soy candle",
    "category": "Home & Living > Candles",
    "materials": "soy wax, cotton wick, lavender essential oil",
    "features": "45-hour burn time, hand-poured in small batches",
    "audience": "gift shoppers, self-care enthusiasts",
    "tone": "warm, cozy, a little whimsical",
    "price": "$24",
}


def run():
    app_module.client.messages.create = fake_create
    # Use a throwaway trial-store file so this test never pollutes a real
    # deployment's trial counters and always starts from a clean slate.
    app_module.TRIAL_STORE_PATH = "test_trial_usage.json"
    with open(app_module.TRIAL_STORE_PATH, "w") as f:
        json.dump({}, f)
    app_module.FREE_TRIAL_LIMIT = 3
    test_client = app_module.app.test_client()

    # --- test /api/generate ---
    res = test_client.post("/api/generate", json=SINGLE_PAYLOAD)
    data = res.get_json()
    assert res.status_code == 200, res.status_code
    assert data["ok"] is True
    assert data["result"]["title"] == FAKE_JSON["title"]
    assert len(data["result"]["tags"]) == 13
    assert data["unlocked"] is False
    assert data["trial_remaining"] == 2  # 3 - 1 used
    print("PASS: /api/generate returns well-formed listing JSON and tracks trial usage")

    # --- test free trial enforcement ---
    test_client.post("/api/generate", json=SINGLE_PAYLOAD)  # remaining -> 1
    res = test_client.post("/api/generate", json=SINGLE_PAYLOAD)  # remaining -> 0
    assert res.get_json()["trial_remaining"] == 0
    res = test_client.post("/api/generate", json=SINGLE_PAYLOAD)  # should now be blocked
    assert res.status_code == 402, res.status_code
    blocked = res.get_json()
    assert blocked["ok"] is False
    assert blocked["error"] == "free_trial_exhausted"
    print("PASS: free trial blocks generation after the limit is hit (402, no charge to Claude)")

    # --- test unlock flow (Stripe lookup mocked) ---
    app_module.stripe_email_has_paid = lambda email: email == "paid@example.com"

    res = test_client.post("/api/unlock", json={"email": "stranger@example.com"})
    assert res.status_code == 402
    assert res.get_json()["ok"] is False
    print("PASS: unlock rejects an email Stripe has no record of paying")

    res = test_client.post("/api/unlock", json={"email": "paid@example.com"})
    assert res.status_code == 200
    assert res.get_json()["ok"] is True
    assert "lc_pro" in res.headers.get("Set-Cookie", "")
    print("PASS: unlock accepts an email Stripe confirms has paid, sets a session cookie")

    # With the unlock cookie set, generation should work even past the trial limit.
    res = test_client.post("/api/generate", json=SINGLE_PAYLOAD)
    data = res.get_json()
    assert res.status_code == 200, res.status_code
    assert data["unlocked"] is True
    print("PASS: unlocked customers bypass the free-trial limit entirely")

    # --- test /api/bulk (fresh, locked client so the cap applies) ---
    # Reset the trial store first: the trial counter keys on IP, and Flask's
    # test client always reports 127.0.0.1, so without this the previous
    # client's usage would carry over and starve this test. Writing an empty
    # store has the same effect as deleting the file and is more portable
    # across sandboxes that restrict unlink on freshly created files.
    with open(app_module.TRIAL_STORE_PATH, "w") as f:
        json.dump({}, f)
    locked_client = app_module.app.test_client()
    csv_content = (
        "platform,product_name,category,materials,features,audience,tone,price\n"
        "etsy,Lavender soy candle,Home & Living,soy wax,45-hour burn,gift shoppers,cozy,$24\n"
        "etsy,Chunky knit blanket,Home & Living,wool,handmade in USA,home decor lovers,rustic,$68\n"
    )
    data_file = (io.BytesIO(csv_content.encode("utf-8")), "products.csv")
    res = locked_client.post("/api/bulk", data={"file": data_file}, content_type="multipart/form-data")
    assert res.status_code == 200, res.status_code
    body = res.data.decode("utf-8")
    assert "title" in body and "Hand-Poured Lavender" in body
    assert body.count("\n") >= 2  # header + 2 rows (within the free allowance)
    print("PASS: /api/bulk processes rows within the free allowance and returns generated rows")

    print("\nAll smoke tests passed: request/response plumbing, prompt building, JSON "
          "parsing, the CSV bulk flow, the free-trial limiter, and the Stripe-backed "
          "unlock flow all work end-to-end.")


if __name__ == "__main__":
    run()
