"""Glue: plan (planner) + live prices (live) + formula (pricing) -> one result.

This is the single function the chatbot calls. All numbers come from here.
"""
import math
import os
from datetime import date, timedelta

import live
from db import load_db, loc_key, ZRH, GVA
from planner import build_plan, itinerary_markdown, HOTEL_CLASS
from pricing import compute_quote, quote_markdown, inr

_DB = None


def _db():
    global _DB
    if _DB is None:
        _DB = load_db()
    return _DB


def plan_and_quote(start_date: str, nights: int, adults: int, child_ages: list[int],
                   budget: str, interests: list[str], origin_airport: str = "COK") -> dict:
    """Build the day-by-day itinerary and the full priced quote using LIVE flight and hotel prices.

    Args:
        start_date: arrival date in Switzerland, YYYY-MM-DD.
        nights: number of nights in Switzerland (days = nights + 1).
        adults: number of adults (age 16+).
        child_ages: list of ages of children, e.g. [8]; empty list if none.
        budget: "budget", "standard" or "luxury".
        interests: e.g. ["lakes", "snow mountains", "towns"].
        origin_airport: IATA code of the home airport, default COK (Kochi).
    """
    db = _db()
    budget = (budget or "standard").lower()
    child_ages = [int(a) for a in (child_ages or [])]
    cfg = db["cfg"]

    car_rate = os.environ.get("CAR_RATE_CHF_PER_DAY")
    if not car_rate:
        return {"status": "needs_car_rate",
                "message": "Car rental price per day (from a car rental website) is not set. "
                           "Set CAR_RATE_CHF_PER_DAY and CAR_RATE_SOURCE before quoting."}
    car_rate = float(car_rate)
    car_source = os.environ.get("CAR_RATE_SOURCE", "car rental website")

    start = date.fromisoformat(start_date)
    end = start + timedelta(days=nights)
    plan = build_plan(db, nights, interests, budget)

    missing, notes = [], []

    # --- live FX ---
    try:
        fx_info = live.chf_inr_rate()
        fx = fx_info["rate"]
        fx_note = f"CHF→INR {fx:.2f} ({fx_info['source']})"
    except live.LiveError as e:
        fx = cfg["sheet_fx"]
        fx_note = f"CHF→INR {fx:.2f} (live rate unavailable; database reference rate used)"
        notes.append(str(e))

    # --- live flights: open-jaw, priced as two one-way searches, per person ---
    flights = {}
    try:
        flights["out"] = live.flight_one_way(origin_airport, "ZRH", start.isoformat())
        flights["back"] = live.flight_one_way("GVA", origin_airport, end.isoformat())
    except live.LiveError as e:
        missing.append(f"Flights: {e}")

    # --- live hotels per stay city ---
    hotel_stays, hotel_lines = [], []
    rooms = max(1, math.ceil(max(adults, 1) / 2)) if (adults or child_ages) else 0
    cursor = start
    for city, n in plan["stays"]:
        ci, co = cursor.isoformat(), (cursor + timedelta(days=n)).isoformat()
        cursor += timedelta(days=n)
        try:
            h = live.hotel_rate(city, ci, co, adults, child_ages, HOTEL_CLASS.get(budget, "3,4"), budget)
            hotel_stays.append({"city": city, "nights": n, "rate_per_night_inr": h["rate_per_night"], "rooms": rooms})
            hotel_lines.append(f"{city}: {h['name']} {inr(h['rate_per_night'])}/night × {n}"
                               + (f" × {rooms} rooms" if rooms > 1 else ""))
        except live.LiveError as e:
            missing.append(f"Hotel in {city}: {e}")

    def _f(f):
        return f"{f['route']} {f['date']} ({f['airlines']}, {f['stops']} stop(s))"
    flights_text = {"out": _f(flights["out"]), "back": _f(flights["back"])} if flights else {}
    itin = itinerary_markdown(db, plan, start_date, flights_text)

    if missing:
        return {"status": "incomplete", "itinerary_markdown": itin, "missing_live_prices": missing,
                "message": "Could not fetch some live prices, so NO total is given. Do not estimate them."}

    q = compute_quote(
        adults=adults, child_ages=child_ages, nights=nights,
        flight_per_person_inr=flights["out"]["price_per_person"] + flights["back"]["price_per_person"],
        hotel_stays=hotel_stays, car_rate_chf_per_day=car_rate, fx_chf_inr=fx,
        vignette_chf=cfg["vignette_chf"], parking_chf_per_night=cfg["parking_chf_per_night"],
        sights=plan["sights"], service_charge_inr=cfg["service_charge_inr"])

    qmd = quote_markdown(
        q, flight_note=f"{inr(flights['out']['price_per_person'])} out + {inr(flights['back']['price_per_person'])} back (live, per person)",
        hotel_lines=hotel_lines, car_rate_chf=car_rate, adults=adults, child_ages=child_ages,
        nights=nights, vignette_chf=cfg["vignette_chf"], parking_chf=cfg["parking_chf_per_night"],
        service_rate=cfg["service_charge_inr"])

    assumptions = [
        f"Live prices fetched via SerpApi Google Flights/Hotels; {fx_note}.",
        f"Car rental CHF {car_rate:g}/day from {car_source} (manual lookup).",
        f"Hotel = {budget} class, one room per 2 adults (rooms: {rooms}); children included in the room search.",
        "Sightseeing = Sheet 2 ticket prices (CHF) × people × live rate; children under 6 free.",
        "Parking per night and vignette are from the database assumptions.",
    ] + notes
    return {"status": "ok", "itinerary_markdown": itin, "quote_markdown": qmd,
            "assumptions": assumptions,
            "totals": {"total_inr": round(q["total"]), "per_person_inr": round(q["per_person"])}}
