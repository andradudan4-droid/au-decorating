import os

import requests

SERPAPI_API_KEY = os.environ.get("SERPAPI_API_KEY")
BUSINESS_NAME = "AU Decorating"

KEYWORDS = [
    "painter portsmouth",
    "decorator portsmouth",
    "painters and decorators portsmouth",
    "house painter portsmouth",
    "painter waterlooville",
    "decorator waterlooville",
    "painters and decorators waterlooville",
    "interior painter portsmouth",
]


def check_ranking(keyword):
    """Return AU Decorating's 1-based position in the Google local 3-pack
    for `keyword`, or None if it isn't found there (or SerpApi isn't
    configured)."""
    if not SERPAPI_API_KEY:
        print("SERPAPI_API_KEY not set, skipping rank check")
        return None

    response = requests.get(
        "https://serpapi.com/search",
        params={
            "engine": "google",
            "q": keyword,
            "location": "Portsmouth,England,United Kingdom",
            "google_domain": "google.co.uk",
            "gl": "uk",
            "hl": "en",
            "api_key": SERPAPI_API_KEY,
        },
        # SerpApi's local-pack (map) searches routinely take well over 8s in
        # practice (confirmed in production: every keyword timed out at 8s).
        # /internal/rank-check now runs the batch off the request thread
        # (see app.py's internal_rank_check), so a generous per-call timeout
        # no longer risks blocking the live chat or tripping gunicorn's
        # request timeout - it only bounds how long one hung connection can
        # delay the rest of the background batch.
        timeout=25,
    )
    response.raise_for_status()
    data = response.json()

    places = data.get("local_results", {}).get("places", [])
    for place in places:
        if BUSINESS_NAME.lower() in place.get("title", "").lower():
            return place.get("position")
    return None
