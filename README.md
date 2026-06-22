# Listing Copilot (MVP)

Turns raw product info into SEO-optimized, human-sounding Etsy/Shopify/Amazon
listings — title, 13 tags, full description, plus a Pinterest description and
Instagram caption so sellers can cross-post in one click.

## Run it locally

```bash
cd listing-copilot
python -m venv venv && source venv/bin/activate   # optional but recommended
pip install -r requirements.txt
cp .env.example .env        # then paste in your real Anthropic API key
export $(cat .env | xargs)  # or use python-dotenv / your platform's env settings
python app.py
```

Open http://localhost:5000 — you'll see a form for a single listing, and a
"Bulk upload" tab that accepts a CSV for processing many products at once.

CSV columns expected for bulk upload:
`platform, product_name, category, materials, features, audience, tone, price`

## Deploying (so real customers can use it)

This is a standard Flask app — it deploys in minutes on any of these:
- **Render.com** (free tier to start): New Web Service → connect this folder →
  build command `pip install -r requirements.txt` → start command
  `gunicorn app:app` → add `ANTHROPIC_API_KEY` as an environment variable.
- **Railway.app** or **Fly.io**: same idea, both have generous free/low-cost tiers.
- **Replit**: paste the files in, add the API key as a Secret, hit Run.

Get an Anthropic API key at https://console.anthropic.com (pay-as-you-go,
no monthly minimum — perfect for testing before you have paying customers).

## What's NOT built yet (intentionally, to keep the MVP lean)

- **Billing/subscriptions** — for the first 10-20 users, manually invoice via
  Stripe Payment Links (5-minute setup, no code) instead of building full
  subscription logic. Wire up real Stripe + user accounts once you've
  validated people will pay.
- **User accounts/login** — fine for a beta where you're hand-onboarding
  early customers; add auth once you outgrow that.
- **Rate limiting / cost caps** — add before opening this to the public, so
  one user can't run up your Anthropic bill. A simple per-IP or per-user
  daily cap is enough at this stage.
- **Shopify/Amazon-specific formatting nuances** — the prompt already adapts
  tone per platform; refine the rules in `SYSTEM_PROMPT` in `app.py` as you
  learn what each platform's sellers actually need.

## Cost to run

Claude API calls are pay-per-use. A single listing generation (title + tags +
description + 2 social captions) runs roughly 1,000-1,500 output tokens —
expect well under $0.01-0.02 per listing generated at current Claude pricing,
so even heavy beta testing should cost only a few dollars.
