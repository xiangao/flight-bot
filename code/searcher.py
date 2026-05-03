import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from amadeus import Client, ResponseError
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


@dataclass
class FlightOffer:
    price: float
    currency: str
    departure_date: str   # first leg departure date (YYYY-MM-DD)
    final_leg_date: str   # last leg departure date (YYYY-MM-DD)
    stops: int            # total connections across all legs
    airline: str          # slash-joined carrier codes


def _make_client() -> Client:
    return Client(
        client_id=os.environ["AMADEUS_CLIENT_ID"],
        client_secret=os.environ["AMADEUS_CLIENT_SECRET"],
    )


def _sample_dates(start: str, end: str, n: int) -> list[date]:
    d_start = date.fromisoformat(start)
    d_end = date.fromisoformat(end)
    span = (d_end - d_start).days
    return [d_start + timedelta(days=round(i * span / (n - 1))) for i in range(n)]


def _extract_cheapest(offers: list[dict], max_stops_per_leg: int) -> FlightOffer | None:
    valid = [
        o for o in offers
        if max(len(it["segments"]) - 1 for it in o["itineraries"]) <= max_stops_per_leg
    ]
    if not valid:
        return None
    cheapest = min(valid, key=lambda o: float(o["price"]["grandTotal"]))
    itins = cheapest["itineraries"]
    carriers = {seg["carrierCode"] for it in itins for seg in it["segments"]}
    return FlightOffer(
        price=float(cheapest["price"]["grandTotal"]),
        currency=cheapest["price"]["currency"],
        departure_date=itins[0]["segments"][0]["departure"]["at"][:10],
        final_leg_date=itins[-1]["segments"][0]["departure"]["at"][:10],
        stops=sum(len(it["segments"]) - 1 for it in itins),
        airline="/".join(sorted(carriers)),
    )


def search_round_trip(route: dict, config: dict) -> FlightOffer | None:
    client = _make_client()
    dates = _sample_dates(config["date_start"], config["date_end"], config["sample_dates"])
    date_end = date.fromisoformat(config["date_end"])
    best: FlightOffer | None = None

    for dep_date in dates:
        for stay in [route["stay_min"], route["stay_max"]]:
            ret_date = dep_date + timedelta(days=stay)
            if ret_date > date_end:
                continue
            try:
                resp = client.shopping.flight_offers_search.get(
                    originLocationCode=route["origin"],
                    destinationLocationCode=route["destination"],
                    departureDate=str(dep_date),
                    returnDate=str(ret_date),
                    adults=1,
                    max=5,
                    currencyCode="USD",
                )
                offer = _extract_cheapest(resp.data, route["max_stops"])
                if offer and (best is None or offer.price < best.price):
                    best = offer
            except ResponseError as e:
                print(f"WARNING [{route['origin']}-{route['destination']} {dep_date}]: {e}")
    return best


def search_multi_city(route: dict, config: dict) -> FlightOffer | None:
    client = _make_client()
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
                body = {
                    "currencyCode": "USD",
                    "originDestinations": [
                        {"id": "1",
                         "originLocationCode": segs[0]["origin"],
                         "destinationLocationCode": segs[0]["destination"],
                         "departureDateTimeRange": {"date": str(dep_date)}},
                        {"id": "2",
                         "originLocationCode": segs[1]["origin"],
                         "destinationLocationCode": segs[1]["destination"],
                         "departureDateTimeRange": {"date": str(mid_date)}},
                        {"id": "3",
                         "originLocationCode": segs[2]["origin"],
                         "destinationLocationCode": segs[2]["destination"],
                         "departureDateTimeRange": {"date": str(ret_date)}},
                    ],
                    "travelers": [{"id": "1", "travelerType": "ADULT"}],
                    "sources": ["GDS"],
                    "searchCriteria": {
                        "maxFlightOffers": 5,
                        "flightFilters": {
                            "connectionRestriction": {
                                "maxNumberOfConnections": route["max_stops"]
                            }
                        },
                    },
                }
                try:
                    resp = client.shopping.flight_offers_search.post(body)
                    offer = _extract_cheapest(resp.data, route["max_stops"])
                    if offer and (best is None or offer.price < best.price):
                        best = offer
                except ResponseError as e:
                    print(f"WARNING [multi-city {dep_date}/{stay1}/{stay2}]: {e}")
    return best
