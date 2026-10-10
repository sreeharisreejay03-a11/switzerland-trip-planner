"""Task 3: the Romford pricing formula. Pure arithmetic - no LLM, no network.

TOTAL = Flight + Hotels + Car + (Vignette + Parking) + Sightseeing + Service charge
"""


def inr(x: float) -> str:
    return "₹{:,.0f}".format(round(x))


def child_ticket_chf(sight: dict, age: int) -> float:
    """Children under 6 are treated as free; 6-15 pay the child price; 16+ pay adult."""
    if age < 6:
        return 0.0
    if age < 16:
        return sight["child_chf"]
    return sight["adult_chf"]


def compute_quote(*, adults: int, child_ages: list, nights: int,
                  flight_per_person_inr: float,
                  hotel_stays: list,            # [{"city","nights","rate_per_night_inr","rooms"}]
                  car_rate_chf_per_day: float,
                  fx_chf_inr: float,
                  vignette_chf: float, parking_chf_per_night: float,
                  sights: list,                 # list of sight dicts from db
                  service_charge_inr: float) -> dict:
    people = adults + len(child_ages)
    car_days = nights  # pick-up on arrival day, drop-off on departure day = `nights` rental days

    flight = flight_per_person_inr * people
    hotels = sum(h["rate_per_night_inr"] * h["rooms"] * h["nights"] for h in hotel_stays)
    car = car_rate_chf_per_day * car_days * fx_chf_inr
    vignette = vignette_chf * fx_chf_inr
    parking = parking_chf_per_night * nights * fx_chf_inr
    tolls_parking = vignette + parking

    sight_rows = []
    sight_total = 0.0
    for s in sights:
        chf = adults * s["adult_chf"] + sum(child_ticket_chf(s, a) for a in child_ages)
        sight_rows.append((s["place"], chf * fx_chf_inr))
        sight_total += chf * fx_chf_inr

    # Service charge: per adult; children under 11 free (a child aged 11+ counts as an adult)
    payers = adults + sum(1 for a in child_ages if a >= 11)
    service = service_charge_inr * payers

    total = flight + hotels + car + tolls_parking + sight_total + service
    return {
        "people": people, "payers": payers, "car_days": car_days,
        "flight": flight, "hotels": hotels, "car": car,
        "vignette": vignette, "parking": parking, "tolls_parking": tolls_parking,
        "sightseeing": sight_total, "sight_rows": sight_rows,
        "service": service, "total": total, "per_person": total / people,
        "fx": fx_chf_inr,
    }


def quote_markdown(q: dict, *, flight_note: str, hotel_lines: list, car_rate_chf: float,
                   adults: int, child_ages: list, nights: int, vignette_chf: float,
                   parking_chf: float, service_rate: float) -> str:
    people = q["people"]
    lines = [
        "| Item | How calculated | Amount (INR) |",
        "|---|---|---:|",
        f"| Flights | {flight_note} × {people} people | {inr(q['flight'])} |",
        f"| Hotels | {'; '.join(hotel_lines)} | {inr(q['hotels'])} |",
        f"| Car rental | CHF {car_rate_chf:,.0f}/day × {q['car_days']} days × {q['fx']:.2f} | {inr(q['car'])} |",
        f"| Vignette, tolls & parking | Vignette CHF {vignette_chf:,.0f} + parking CHF {parking_chf:,.0f} × {nights} nights "
        f"(CHF→INR {q['fx']:.2f}) | {inr(q['tolls_parking'])} |",
        f"| Sightseeing | Sheet 2 tickets × {adults} adults"
        + (f" + {len(child_ages)} child(ren)" if child_ages else "") + f" | {inr(q['sightseeing'])} |",
        f"| Romford service charge | {inr(service_rate)} × {q['payers']} adult(s); children under 11 free | {inr(q['service'])} |",
        f"| **TOTAL** | | **{inr(q['total'])}** |",
        f"| **Price per person** | total ÷ {people} | **{inr(q['per_person'])}** |",
    ]
    return "\n".join(lines)
