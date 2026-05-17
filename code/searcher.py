import json
import os
import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

SERPAPI_URL = "https://serpapi.com/search.json"
IGNAV_URL = "https://ignav.com/api"
BASE_DIR = Path(__file__).parent.parent
CACHE_DIR = BASE_DIR / "data" / "api_cache"
EXCLUDED_AIRLINES = {"Turkish Airlines"}


@dataclass
class FlightOffer:
    price: float
    currency: str
    departure_date: str   # first leg departure date (YYYY-MM-DD)
    final_leg_date: str   # last leg departure date (YYYY-MM-DD)
    stops: int            # stops on outbound/first leg
    airline: str          # primary airline of first leg
    details: str = ""     # human-readable itinerary details


def _destination_options(route: dict) -> list[tuple[str, str]]:
    if "destinations" not in route:
        return [(route["destination"], route.get("destination_name", route["destination"]))]

    options = []
    for item in route["destinations"]:
        if isinstance(item, str):
            options.append((item, item))
        else:
            options.append((item["code"], item.get("name", item["code"])))
    return options


def _stay_options(route: dict) -> list[int]:
    if "stay_step" in route:
        step = max(int(route["stay_step"]), 1)
        return list(range(int(route["stay_min"]), int(route["stay_max"]) + 1, step))
    return list(dict.fromkeys([route["stay_min"], route["stay_max"]]))


def _annotate_destination(offer: FlightOffer, destination_name: str) -> FlightOffer:
    offer.airline = f"{offer.airline} to {destination_name}"
    return offer


def _api_key() -> str:
    key = os.environ.get("SERPAPI_KEY", "")
    if not key:
        raise RuntimeError("SERPAPI_KEY not set in environment")
    return key


def _ignav_api_key() -> str:
    key = os.environ.get("IGNAV_API_KEY", "")
    if not key:
        raise RuntimeError("IGNAV_API_KEY not set in environment")
    return key


def _provider(config: dict) -> str:
    return str(config.get("provider") or os.environ.get("FLIGHT_PROVIDER", "serpapi")).lower()


def _sample_dates(start: str, end: str, n: int) -> list[date]:
    d_start = date.fromisoformat(start)
    d_end = date.fromisoformat(end)
    if n <= 1:
        return [d_start]
    span = (d_end - d_start).days
    return [d_start + timedelta(days=round(i * span / (n - 1))) for i in range(n)]


def _duration_label(minutes: int | float | None) -> str:
    if minutes is None:
        return ""
    total = int(minutes)
    hours, mins = divmod(total, 60)
    return f"{hours}h {mins:02d}m" if mins else f"{hours}h"


def _time_label(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("T", " ")[:16]


def _segment_airport(segment: dict, side: str) -> str:
    return (
        segment.get(f"{side}_airport")
        or segment.get(f"{side}_airport_code")
        or segment.get(f"{side}_airport", {}).get("id")
        or ""
    )


def _segment_time(segment: dict, side: str) -> str:
    value = segment.get(f"{side}_time_local") or segment.get(f"{side}_time_utc")
    if value:
        return _time_label(value)
    airport = segment.get(f"{side}_airport")
    if isinstance(airport, dict):
        return _time_label(airport.get("time"))
    return ""


def _format_leg(label: str, leg: dict | None) -> str:
    if not leg:
        return ""

    segments = leg.get("segments") or []
    if not segments:
        return ""

    carrier = leg.get("carrier") or segments[0].get("operating_carrier_name") or segments[0].get("airline") or "Unknown"
    stops = max(len(segments) - 1, 0)
    duration = _duration_label(leg.get("duration_minutes") or leg.get("duration"))
    route_bits = []

    for segment in segments:
        dep_airport = _segment_airport(segment, "departure")
        arr_airport = _segment_airport(segment, "arrival")
        dep_time = _segment_time(segment, "departure")
        arr_time = _segment_time(segment, "arrival")
        flight = " ".join(
            str(x) for x in [
                segment.get("marketing_carrier_code") or segment.get("airline"),
                segment.get("flight_number"),
            ] if x
        )
        route_bits.append(f"{dep_airport} {dep_time} -> {arr_airport} {arr_time}".strip())
        if flight:
            route_bits[-1] = f"{route_bits[-1]} ({flight})"

    pieces = [f"{label}: {carrier}, {stops} stop(s)"]
    if duration:
        pieces[0] += f", {duration}"
    pieces.extend(f"  {bit}" for bit in route_bits)
    return "\n".join(pieces)


def _search(params: dict, cache_hours: float = 6) -> dict:
    cache_key = {k: v for k, v in params.items() if k != "api_key"}
    cache_path = _cache_path("serpapi", "google_flights", cache_key)
    cached = _read_cache(cache_path, cache_hours)
    if cached is not None:
        return cached

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
    data = resp.json()
    _write_cache(cache_path, data)
    return data


def _cheapest_offer(data: dict) -> FlightOffer | None:
    offers = data.get("best_flights", []) + data.get("other_flights", [])
    valid = [
        o for o in offers
        if "price" in o and o.get("flights")
        and o["flights"][0].get("airline") not in EXCLUDED_AIRLINES
    ]
    if not valid:
        return None
    best = min(valid, key=lambda o: o["price"])
    flights = best["flights"]
    first_flight = flights[0]
    departure_date = first_flight["departure_airport"]["time"][:10]
    stops = len(best.get("layovers", []))
    airline = first_flight.get("airline", "Unknown")
    segments = []
    for flight in flights:
        segments.append({
            "airline": flight.get("airline"),
            "departure_airport": flight.get("departure_airport", {}),
            "arrival_airport": flight.get("arrival_airport", {}),
            "duration": flight.get("duration"),
        })
    outbound = {
        "carrier": airline,
        "duration_minutes": best.get("total_duration"),
        "segments": segments,
    }
    return FlightOffer(
        price=float(best["price"]),
        currency="USD",
        departure_date=departure_date,
        final_leg_date="",   # filled in by caller
        stops=stops,
        airline=airline,
        details=_format_leg("Outbound", outbound),
    )


def _cache_path(provider: str, endpoint: str, payload: dict) -> Path:
    raw = json.dumps(
        {"provider": provider, "endpoint": endpoint, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return CACHE_DIR / provider / f"{digest}.json"


def _read_cache(path: Path, ttl_hours: float) -> dict | None:
    if ttl_hours <= 0 or not path.exists():
        return None
    age_hours = (datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)).total_seconds() / 3600
    if age_hours > ttl_hours:
        return None
    with open(path) as f:
        return json.load(f)


def _write_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)


def _ignav_post(endpoint: str, payload: dict, config: dict) -> dict:
    cache_hours = float(config.get("cache_hours", 6))
    cache_path = _cache_path("ignav", endpoint, payload)
    cached = _read_cache(cache_path, cache_hours)
    if cached is not None:
        return cached

    resp = requests.post(
        f"{IGNAV_URL}{endpoint}",
        json=payload,
        headers={"X-Api-Key": _ignav_api_key(), "Content-Type": "application/json"},
        timeout=45,
    )
    resp.raise_for_status()
    data = resp.json()
    _write_cache(cache_path, data)
    return data


def _ignav_request_base(config: dict, max_stops: int | None) -> dict:
    body = {
        "adults": int(config.get("adults", 1)),
        "cabin_class": config.get("cabin_class", "economy"),
        "market": config.get("market", "US"),
        "allow_self_transfer": bool(config.get("allow_self_transfer", True)),
    }
    if max_stops is not None:
        body["max_stops"] = max(0, min(int(max_stops), 2))
    for key in ("children", "infants_in_seat", "infants_on_lap", "min_carry_on_bags",
                "min_checked_bags", "max_price", "airlines_include", "airlines_exclude"):
        if key in config:
            body[key] = config[key]
    return body


def _ignav_cheapest_offer(data: dict) -> FlightOffer | None:
    itineraries = data.get("itineraries", [])
    valid = [
        item for item in itineraries
        if item.get("price", {}).get("amount") is not None
        and item.get("outbound", {}).get("segments")
        and item.get("outbound", {}).get("carrier") not in EXCLUDED_AIRLINES
    ]
    if not valid:
        return None

    best = min(valid, key=lambda item: float(item["price"]["amount"]))
    outbound = best["outbound"]
    segments = outbound["segments"]
    first_segment = segments[0]
    airline = outbound.get("carrier") or first_segment.get("marketing_carrier_code") or "Unknown"

    return FlightOffer(
        price=float(best["price"]["amount"]),
        currency=best["price"].get("currency", "USD"),
        departure_date=first_segment["departure_time_local"][:10],
        final_leg_date="",
        stops=max(len(segments) - 1, 0),
        airline=airline,
        details="\n".join(
            part for part in [
                _format_leg("Outbound", best.get("outbound")),
                _format_leg("Inbound", best.get("inbound")),
            ] if part
        ),
    )


def _search_ignav_one_way(
    origin: str,
    destination: str,
    dep_date: date,
    config: dict,
    max_stops: int | None,
) -> FlightOffer | None:
    payload = _ignav_request_base(config, max_stops)
    payload.update({
        "origin": origin,
        "destination": destination,
        "departure_date": str(dep_date),
    })
    data = _ignav_post("/fares/one-way", payload, config)
    return _ignav_cheapest_offer(data)


def search_round_trip_serpapi(route: dict, config: dict, stops_filter: int = 2) -> FlightOffer | None:
    dates = _sample_dates(config["date_start"], config["date_end"], config["sample_dates"])
    date_end = date.fromisoformat(config["date_end"])
    best: FlightOffer | None = None

    for destination, destination_name in _destination_options(route):
        for dep_date in dates:
            for stay in _stay_options(route):
                ret_date = dep_date + timedelta(days=stay)
                if ret_date > date_end:
                    continue
                try:
                    data = _search({
                        "type": "1",
                        "departure_id": route["origin"],
                        "arrival_id": destination,
                        "outbound_date": str(dep_date),
                        "return_date": str(ret_date),
                        "stops": str(stops_filter),
                    }, cache_hours=float(config.get("cache_hours", 6)))
                    offer = _cheapest_offer(data)
                    if offer:
                        offer.final_leg_date = str(ret_date)
                        _annotate_destination(offer, destination_name)
                        if best is None or offer.price < best.price:
                            best = offer
                except Exception as e:
                    print(f"WARNING [{route['origin']}-{destination} {dep_date}]: {e}")
    return best


def search_round_trip_ignav(route: dict, config: dict, max_stops: int | None = 1) -> FlightOffer | None:
    dates = _sample_dates(config["date_start"], config["date_end"], config["sample_dates"])
    date_end = date.fromisoformat(config["date_end"])
    best: FlightOffer | None = None

    for destination, destination_name in _destination_options(route):
        for dep_date in dates:
            for stay in _stay_options(route):
                ret_date = dep_date + timedelta(days=stay)
                if ret_date > date_end:
                    continue
                try:
                    payload = _ignav_request_base(config, max_stops)
                    payload.update({
                        "origin": route["origin"],
                        "destination": destination,
                        "departure_date": str(dep_date),
                        "return_date": str(ret_date),
                    })
                    data = _ignav_post("/fares/round-trip", payload, config)
                    offer = _ignav_cheapest_offer(data)
                    if offer:
                        offer.final_leg_date = str(ret_date)
                        _annotate_destination(offer, destination_name)
                        if best is None or offer.price < best.price:
                            best = offer
                except Exception as e:
                    print(f"WARNING [Ignav {route['origin']}-{destination} {dep_date}]: {e}")
    return best


def search_round_trip(route: dict, config: dict, stops_filter: int = 2) -> FlightOffer | None:
    if _provider(config) == "ignav":
        return search_round_trip_ignav(route, config, max_stops=stops_filter)
    return search_round_trip_serpapi(route, config, stops_filter=stops_filter)


def search_multi_city_serpapi(route: dict, config: dict, stops_filter: int = 2) -> FlightOffer | None:
    dates = _sample_dates(config["date_start"], config["date_end"], config["sample_dates"])
    date_end = date.fromisoformat(config["date_end"])
    segs = route["segments"]
    best: FlightOffer | None = None

    for dep_date in dates:
        for stay1 in _stay_options(segs[0]):
            for stay2 in _stay_options(segs[1]):
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
                        "stops": str(stops_filter),
                    }, cache_hours=float(config.get("cache_hours", 6)))
                    offer = _cheapest_offer(data)
                    if offer:
                        offer.final_leg_date = str(ret_date)
                        if best is None or offer.price < best.price:
                            best = offer
                except Exception as e:
                    print(f"WARNING [multi-city {dep_date}/{stay1}/{stay2}]: {e}")
    return best


def search_multi_city_ignav(route: dict, config: dict, max_stops: int | None = 1) -> FlightOffer | None:
    dates = _sample_dates(config["date_start"], config["date_end"], config["sample_dates"])
    date_end = date.fromisoformat(config["date_end"])
    segs = route["segments"]
    best: FlightOffer | None = None

    for dep_date in dates:
        for stay1 in _stay_options(segs[0]):
            for stay2 in _stay_options(segs[1]):
                mid_date = dep_date + timedelta(days=stay1)
                ret_date = mid_date + timedelta(days=stay2)
                if ret_date > date_end:
                    continue
                try:
                    leg1 = _search_ignav_one_way(segs[0]["origin"], segs[0]["destination"], dep_date, config, max_stops)
                    leg2 = _search_ignav_one_way(segs[1]["origin"], segs[1]["destination"], mid_date, config, max_stops)
                    leg3 = _search_ignav_one_way(segs[2]["origin"], segs[2]["destination"], ret_date, config, max_stops)
                    if not leg1 or not leg2 or not leg3:
                        continue
                    currency = leg1.currency
                    if len({leg1.currency, leg2.currency, leg3.currency}) != 1:
                        continue
                    offer = FlightOffer(
                        price=leg1.price + leg2.price + leg3.price,
                        currency=currency,
                        departure_date=leg1.departure_date,
                        final_leg_date=str(ret_date),
                        stops=max(leg1.stops, leg2.stops, leg3.stops),
                        airline=" + ".join([leg1.airline, leg2.airline, leg3.airline]),
                        details="\n\n".join(
                            part for part in [
                                f"Leg 1:\n{leg1.details}" if leg1.details else "",
                                f"Leg 2:\n{leg2.details}" if leg2.details else "",
                                f"Leg 3:\n{leg3.details}" if leg3.details else "",
                            ] if part
                        ),
                    )
                    if best is None or offer.price < best.price:
                        best = offer
                except Exception as e:
                    print(f"WARNING [Ignav multi-city {dep_date}/{stay1}/{stay2}]: {e}")
    return best


def search_multi_city(route: dict, config: dict, stops_filter: int = 2) -> FlightOffer | None:
    if _provider(config) == "ignav":
        return search_multi_city_ignav(route, config, max_stops=stops_filter)
    return search_multi_city_serpapi(route, config, stops_filter=stops_filter)
