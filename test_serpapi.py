"""
Quick proof-of-concept test for SerpAPI Google Flights.
Run this BEFORE updating searcher.py to confirm the API works.

Usage:
  source .venv/bin/activate
  SERPAPI_KEY=your_key_here python test_serpapi.py
"""

import json
import os
import sys
import urllib.request
import urllib.parse

API_KEY = os.environ.get("SERPAPI_KEY")
if not API_KEY:
    print("ERROR: Set SERPAPI_KEY env var first.")
    print("  export SERPAPI_KEY=your_key_here")
    sys.exit(1)

BASE = "https://serpapi.com/search.json"


def get(params: dict) -> dict:
    params["api_key"] = API_KEY
    params["engine"] = "google_flights"
    params["currency"] = "USD"
    params["hl"] = "en"
    params["adults"] = "1"
    url = BASE + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def cheapest(data: dict) -> tuple[float, str] | None:
    offers = data.get("best_flights", []) + data.get("other_flights", [])
    if not offers:
        return None
    best = min(offers, key=lambda o: o.get("price", 9999999))
    airlines = {f["airline"] for f in best.get("flights", [])}
    return best["price"], "/".join(sorted(airlines))


print("=" * 55)
print("TEST 1: Round-trip BOS → HKG (stay 18 days, max 1 stop)")
print("=" * 55)
try:
    data = get({
        "type": "1",
        "departure_id": "BOS",
        "arrival_id": "HKG",
        "outbound_date": "2026-09-15",
        "return_date": "2026-10-03",
        "stops": "2",          # 1 stop or fewer
        "sort_by": "2",        # sort by price
    })
    result = cheapest(data)
    if result:
        price, airline = result
        print(f"  PASS: ${price:,.0f} — {airline}")
    else:
        print("  WARNING: No flights found (try different dates)")
except Exception as e:
    print(f"  FAIL: {e}")

print()
print("=" * 55)
print("TEST 2: Multi-city BOS→TYO, OSA→HKG, HKG→BOS")
print("=" * 55)
multi_city = json.dumps([
    {"departure_id": "BOS", "arrival_id": "TYO", "date": "2026-09-15"},
    {"departure_id": "OSA", "arrival_id": "HKG", "date": "2026-09-23"},
    {"departure_id": "HKG", "arrival_id": "BOS", "date": "2026-10-08"},
])
try:
    data = get({
        "type": "3",
        "multi_city_json": multi_city,
        "stops": "2",
        "sort_by": "2",
    })
    result = cheapest(data)
    if result:
        price, airline = result
        print(f"  PASS: ${price:,.0f} — {airline}")
    else:
        print("  WARNING: No flights found (try different dates)")
    # Show raw structure so we know what fields to parse
    sample = (data.get("best_flights") or data.get("other_flights") or [{}])[0]
    print(f"  Response keys in first offer: {list(sample.keys())}")
except Exception as e:
    print(f"  FAIL: {e}")

print()
print("Done. If both tests show PASS, the API works — proceed with updating searcher.py.")
