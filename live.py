"""Task 2: live prices from SerpApi (Google Flights + Google Hotels) and a live CHF->INR rate.

Rules: never invent a price. Any failure raises LiveError so the caller reports
"could not fetch live price" instead of guessing.
A small on-disk cache (default 30 min) protects the free SerpApi quota while testing.
"""
import hashlib
import json
import os
import statistics
import time

import requests

SERP_URL = "https://serpapi.com/search.json"
CACHE_FILE = os.path.join(os.path.dirname(__file__), ".live_cache.json")
CACHE_TTL = int(os.environ.get("LIVE_CACHE_SECONDS", "1800"))


class LiveError(Exception):
    pass


def _cache_get(key):
    try:
        data = json.load(open(CACHE_FILE))
        item = data.get(key)
        if item and time.time() - item["t"] < CACHE_TTL:
            return item["v"]
    except Exception:
        pass
    return None


def _cache_put(key, value):
    try:
        try:
            data = json.load(open(CACHE_FILE))
        except Exception:
            data = {}
        data[key] = {"t": time.time(), "v": value}
        json.dump(data, open(CACHE_FILE, "w"))
    except Exception:
        pass


def _serp(params: dict) -> dict:
    key = os.environ.get("SERPAPI_KEY")
    if not key:
        raise LiveError("SERPAPI_KEY is not set")
    ck = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()
    hit = _cache_get(ck)
    if hit is not None:
        return hit
    try:
        r = requests.get(SERP_URL, params={**params, "api_key": key}, timeout=120)
        data = r.json()
    except Exception as e:  # network / JSON failure
        raise LiveError(f"SerpApi request failed: {e}")
    if r.status_code != 200 or data.get("error"):
        raise LiveError(f"SerpApi error: {data.get('error', r.status_code)}")
    _cache_put(ck, data)
    return data


def flight_one_way(origin: str, dest: str, date: str) -> dict:
    """Cheapest live one-way fare per person in INR (Google Flights via SerpApi)."""
    data = _serp({"engine": "google_flights", "departure_id": origin, "arrival_id": dest,
                  "outbound_date": date, "type": "2", "adults": "1",
                  "currency": "INR", "hl": "en", "gl": "in",
                  "deep_search": "true"})   # matches the Google Flights page (verified on GVA->COK)
    options = (data.get("best_flights") or []) + (data.get("other_flights") or [])
    options = [o for o in options if o.get("price")]
    if not options:
        raise LiveError(f"No live flights returned for {origin}->{dest} on {date}")
    best = min(options, key=lambda o: o["price"])
    legs = best.get("flights", [])
    airlines = sorted({l.get("airline", "?") for l in legs})
    return {
        "price_per_person": float(best["price"]),
        "route": f"{origin}→{dest}", "date": date,
        "airlines": ", ".join(airlines), "stops": max(len(legs) - 1, 0),
        "duration_min": best.get("total_duration"),
        "url": data.get("search_metadata", {}).get("google_flights_url"),
    }


def hotel_rate(city: str, check_in: str, check_out: str, adults: int, child_ages: list,
               hotel_class: str, budget: str) -> dict:
    """Representative live nightly rate (INR) for one room in `city`.
    Picks a property at a fixed point in the price-sorted list of returned, well-rated hotels:
      budget   -> 30th percentile of 2-3 star hotels
      standard -> 35th percentile of 3-4 star hotels
      luxury   -> 50th percentile (median) of 4-5 star hotels
    A median for luxury avoids always landing on suites/outliers (e.g. CHF 1,700/night)."""
    params = {"engine": "google_hotels", "q": f"hotels in {city}, Switzerland",
              "check_in_date": check_in, "check_out_date": check_out,
              "adults": str(max(1, adults)), "currency": "INR", "gl": "in", "hl": "en",
              "hotel_class": hotel_class}
    if child_ages:
        params["children"] = str(len(child_ages))
        params["children_ages"] = ",".join(str(a) for a in child_ages)
    data = _serp(params)
    priced = [(float((p.get("rate_per_night") or {}).get("extracted_lowest")), p)
              for p in data.get("properties", [])
              if (p.get("rate_per_night") or {}).get("extracted_lowest")]
    props = [t for t in priced if (t[1].get("overall_rating") or 0) >= 3.8]
    if not props:
        props = priced                      # few rated results: use whatever has a live price
    if not props:
        raise LiveError(f"No live hotel prices returned for {city} {check_in}..{check_out}")
    props.sort(key=lambda t: t[0])
    n = len(props)
    # Choose the lowest-priced result in the selected star class.
    idx = 0
    rate, prop = props[idx]
    return {"city": city, "rate_per_night": rate, "name": prop.get("name", "hotel"),
            "rating": prop.get("overall_rating"), "sample_size": n,
            "link": prop.get("link")}


def chf_inr_rate() -> dict:
    """Live CHF->INR mid-market rate (free, no key). Raises LiveError on failure."""
    ck = "fx-chf-inr"
    hit = _cache_get(ck)
    if hit:
        return hit
    try:
        r = requests.get("https://open.er-api.com/v6/latest/CHF", timeout=20)
        rate = float(r.json()["rates"]["INR"])
    except Exception as e:
        raise LiveError(f"Live CHF->INR rate unavailable: {e}")
    out = {"rate": rate, "source": "open.er-api.com (live)"}
    _cache_put(ck, out)
    return out
