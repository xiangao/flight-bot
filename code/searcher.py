import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

SERPAPI_URL = "https://serpapi.com/search.json"


@dataclass
class FlightOffer:
    price: float
    currency: str
    departure_date: str   # first leg departure date (YYYY-MM-DD)
    final_leg_date: str   # last leg departure date (YYYY-MM-DD)
    stops: int            # stops on outbound/first leg
    airline: str          # primary airline of first leg


def _api_key() -> str:
    key = os.environ.get("SERPAPI_KEY", "")
    if not key:
        raise RuntimeError("SERPAPI_KEY not set in environment")
    return key


def _sample_dates(start: str, end: str, n: int) -> list[date]:
    d_start = date.fromisoformat(start)
    d_end = date.fromisoformat(end)
    span = (d_end - d_start).days
    return [d_start + timedelta(days=round(i * span / (n - 1))) for i in range(n)]


def _search(params: dict) -> dict:
    params.update({
        "engine": "google_flights",
        "api_key": _api_key(),
        "currency": "USD",
        "hl": "en",
        "adults": "1",
        "sort_by": "2",   # sort by price
    })
    resp = requests.get(SERPAPI_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _cheapest_offer(data: dict) -> FlightOffer | None:
    offers = data.get("best_flights", []) + data.get("other_flights", [])
    valid = [o for o in offers if "price" in o and o.get("flights")]
    if not valid:
        return None
    best = min(valid, key=lambda o: o["price"])
    first_flight = best["flights"][0]
    departure_date = first_flight["departure_airport"]["time"][:10]
    stops = len(best.get("layovers", []))
    airline = first_flight.get("airline", "Unknown")
    return FlightOffer(
        price=float(best["price"]),
        currency="USD",
        departure_date=departure_date,
        final_leg_date="",   # filled in by caller
        stops=stops,
        airline=airline,
    )


def search_round_trip(route: dict, config: dict) -> FlightOffer | None:
    dates = _sample_dates(config["date_start"], config["date_end"], config["sample_dates"])
    date_end = date.fromisoformat(config["date_end"])
    best: FlightOffer | None = None

    for dep_date in dates:
        for stay in [route["stay_min"], route["stay_max"]]:
            ret_date = dep_date + timedelta(days=stay)
            if ret_date > date_end:
                continue
            try:
                data = _search({
                    "type": "1",
                    "departure_id": route["origin"],
                    "arrival_id": route["destination"],
                    "outbound_date": str(dep_date),
                    "return_date": str(ret_date),
                    "stops": "2",   # 1 stop or fewer
                })
                offer = _cheapest_offer(data)
                if offer:
                    offer.final_leg_date = str(ret_date)
                    if best is None or offer.price < best.price:
                        best = offer
            except Exception as e:
                print(f"WARNING [{route['origin']}-{route['destination']} {dep_date}]: {e}")
    return best


def search_multi_city(route: dict, config: dict) -> FlightOffer | None:
    dates = _sample_dates(config["date_start"], config["date_end"], config["sample_dates"])
    date_end = date.fromisoformat(config["date_end"])
    segs = route["segments"]
    best: FlightOffer | None = None

    for dep_date in dates:
        for stay1 in [segs[0]["stay_min"], segs[0]["stay_max"]]:
            for stay2 in [segs[1]["stay_min"], segs[1]["stay_max"]]:
                mid_date = dep_date + timedelta(days=stay1)
                ret_date = mid_date + timedelta(days=stay2)
                if ret_date > date_end:
                    continue
                multi_city_json = json.dumps([
                    {"departure_id": segs[0]["origin"],
                     "arrival_id": segs[0]["destination"],
                     "date": str(dep_date)},
                    {"departure_id": segs[1]["origin"],
                     "arrival_id": segs[1]["destination"],
                     "date": str(mid_date)},
                    {"departure_id": segs[2]["origin"],
                     "arrival_id": segs[2]["destination"],
                     "date": str(ret_date)},
                ])
                try:
                    data = _search({
                        "type": "3",
                        "multi_city_json": multi_city_json,
                        "stops": "2",   # 1 stop or fewer per leg
                    })
                    offer = _cheapest_offer(data)
                    if offer:
                        offer.final_leg_date = str(ret_date)
                        if best is None or offer.price < best.price:
                            best = offer
                except Exception as e:
                    print(f"WARNING [multi-city {dep_date}/{stay1}/{stay2}]: {e}")
    return best
