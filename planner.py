"""Picks stay cities, nights, sightseeing and daily drives from the database.

Deterministic on purpose: the LLM presents the plan, it does not invent it.
Every trip flies into Zurich (ZRH) and out of Geneva (GVA) (open-jaw), so the
route runs west-to-east-to-west without backtracking.
"""
import re

from db import drive, fmt_minutes, loc_key, ZRH, GVA

# Interest words -> sightseeing types in Sheet 2
INTEREST_TYPES = {
    "lake": {"lake"}, "lakes": {"lake"},
    "snow": {"snow", "mountain"}, "mountain": {"snow", "mountain"}, "mountains": {"snow", "mountain"},
    "town": {"town"}, "towns": {"town"}, "city": {"town"}, "culture": {"town"},
}
BUDGET_CAP_CHF = {"budget": 60, "standard": 140, "luxury": 10_000}   # max adult ticket per sight
HOTEL_CLASS = {"budget": "3", "standard": "4", "luxury": "4,5"}

NEARBY = {
    "Grindelwald": ["Interlaken"], "Interlaken": ["Grindelwald"], "Lucerne": ["Engelberg"],
    "Engelberg": ["Lucerne"], "Montreux": ["Lausanne"], "Lausanne": ["Montreux"], "Geneva": ["Lausanne"],
}

# Stay-city order templates (ZRH in ... GVA out). Extra nights go to the first cities in EXTRA_ORDER.
ROUTES = {
    "lake+snow": ["Zurich", "Lucerne", "Grindelwald", "Zermatt", "Montreux"],
    "snow":      ["Zurich", "Engelberg", "Grindelwald", "Zermatt", "Montreux"],
    "lake":      ["Zurich", "Lucerne", "Interlaken", "Montreux", "Geneva"],
    "town":      ["Zurich", "Lucerne", "Lausanne", "Geneva"],
}
DROP_ORDER = ["Zurich", "Geneva", "Montreux", "Zermatt", "Lausanne", "Engelberg", "Interlaken", "Lucerne", "Grindelwald"]
EXTRA_ORDER = ["Grindelwald", "Lucerne", "Zermatt", "Interlaken", "Montreux", "Lausanne", "Engelberg", "Geneva", "Zurich"]


def max_hours(t: str) -> float:
    """Upper bound of a 'Time needed' string, in hours ('1-2 hrs' -> 2, '45 min' -> 0.75, 'Full day' -> 8)."""
    t = t.lower()
    if "full" in t:
        return 8.0
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t.split("incl")[0])]
    if not nums:
        return 3.0
    return max(nums) / 60 if ("min" in t and "hr" not in t) else max(nums)


def wanted_types(interests):
    types = set()
    for w in interests:
        w = w.strip().lower()
        if "lake" in w or "water" in w or "boat" in w:
            types.add("lake")
        if "snow" in w or "mountain" in w or "glacier" in w or "alps" in w or "ski" in w:
            types |= {"snow", "mountain"}
        if "town" in w or "city" in w or "culture" in w or "museum" in w or "history" in w:
            types.add("town")
    return types or {"lake", "mountain", "snow", "town"}


def pick_route_key(types):
    if {"lake"} & types and {"snow", "mountain"} & types:
        return "lake+snow"
    if {"snow", "mountain"} & types:
        return "snow"
    if "lake" in types:
        return "lake"
    return "town"


def allocate_nights(route_key, nights):
    route = list(ROUTES[route_key])
    while len(route) > nights:                       # too many cities for the nights: drop least useful
        for c in DROP_ORDER:
            if c in route:
                route.remove(c)
                break
    alloc = {c: 1 for c in route}
    extra = nights - len(route)
    i = 0
    order = [c for c in EXTRA_ORDER if c in route]
    while extra > 0:
        alloc[order[i % len(order)]] += 1
        i += 1
        extra -= 1
    return [(c, alloc[c]) for c in route]


def build_plan(db, nights, interests, budget):
    budget = budget.lower()
    types = wanted_types(interests)
    stays = allocate_nights(pick_route_key(types), nights)
    cap = BUDGET_CAP_CHF.get(budget, 140)

    # Candidate sights by city; paid + interest-matching first, then free ones
    used = set()

    def candidates(city):
        names = [city] + NEARBY.get(city, [])
        out = []
        for s in db["sights"]:
            if s["city"] in names and s["type"] in types and s["adult_chf"] <= cap and s["place"] not in used:
                out.append((names.index(s["city"]), -s["adult_chf"], s))
        out.sort(key=lambda t: (t[0], t[1]))
        return [t[2] for t in out]

    # Night-by-night location list
    nightly = []
    for city, n in stays:
        nightly += [city] * n

    days, chosen = [], []
    prev = ZRH
    for d in range(1, nights + 2):
        city = nightly[d - 1] if d <= nights else None
        dest = loc_key(city) if city else GVA
        dr = drive(db, prev, dest)
        mins = dr[1] if dr else 0
        cap_hours = 0 if d == nights + 1 else (3.5 if (d == 1 or mins >= 120) else 6.5)
        day_sights, hours = [], 0.0
        if city:
            for s in candidates(city):
                h = max_hours(s["time"])
                if len(day_sights) >= 2 or (day_sights and hours + h > cap_hours):
                    continue
                if not day_sights and h > cap_hours and d == 1:
                    continue                      # no big excursion on arrival day
                day_sights.append(s)
                used.add(s["place"])
                hours += h
        if city and not day_sights and d != 1:
            names = [city] + NEARBY.get(city, [])
            for s in db["sights"]:
                if s["city"] in names and s["adult_chf"] <= cap and s["place"] not in used:
                    day_sights.append(s); used.add(s["place"]); break
        chosen += day_sights
        days.append({"day": d, "from": prev, "to": dest, "drive": dr, "city": city, "sights": day_sights})
        prev = dest
    return {"stays": stays, "days": days, "sights": chosen, "types": sorted(types)}


def city_free(d):
    return d["city"] is not None and d["day"] != 1 and not d["sights"]


def itinerary_markdown(db, plan, start_date_str, flights):
    lines = [f"**Route:** " + " → ".join(f"{c} ({n}N)" for c, n in plan["stays"]) +
             "  \n*Fly into Zurich (ZRH), drive, fly home from Geneva (GVA).*", ""]
    for d in plan["days"]:
        head = f"**Day {d['day']}"
        head += f" – {d['city']}**" if d["city"] else " – Departure**"
        lines.append(head)
        if d["drive"]:
            km, mins = d["drive"]
            lines.append(f"- 🚗 Drive: {d['from']} → {d['to']}: {km} km, about {fmt_minutes(mins)}")
        for s in d["sights"]:
            price = "free" if s["adult_chf"] == 0 else f"CHF {s['adult_chf']:g} adult"
            lines.append(f"- {s['place']} ({s['time']}, {price})")
        if city_free(d):
            lines.append("- Free day: relax, stroll the town, or enjoy the hotel area")
        if d["day"] == 1 and flights.get("out"):
            lines.append(f"- ✈️ Arrive: {flights['out']}")
        if d["city"] is None and flights.get("back"):
            lines.append(f"- ✈️ Depart: {flights['back']}")
        lines.append("")
    return "\n".join(lines)
