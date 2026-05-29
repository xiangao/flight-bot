import json
import os
import hashlib
from dataclasses import dataclass, field
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
class SegmentInfo:
    flight: str        # e.g. "CX 811"
    from_airport: str  # e.g. "BOS"
    to_airport: str    # e.g. "HKG"
    dep_local: str     # e.g. "2026-10-15 01:45"
    arr_local: str     # e.g. "2026-10-16 05:00"
    duration_min: int
    aircraft: str      # e.g. "Airbus A350"
    layover_min: int   # minutes to next segment; 0 if this is the last segment


@dataclass
class FlightOffer:
    price: float
    currency: str
    departure_date: str   # first leg departure date (YYYY-MM-DD)
    final_leg_date: str   # last leg departure date (YYYY-MM-DD)
    stops: int            # stops on outbound/first leg
    airline: str          # primary airline of first leg
    details: str = ""     # human-readable itinerary (for CSV / notifications)
    outbound_segments: list = field(default_factory=list)   # list[SegmentInfo]
    inbound_segments: list = field(default_factory=list)    # list[SegmentInfo]
    outbound_duration_min: int | None = None
    inbound_duration_min: int | None = None


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
        "sort_by": "2",
    })
    resp = requests.get(SERPAPI_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    _write_cache(cache_path, data)
    return data


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
    # Only send max_stops when > 0 (server-side 0 breaks Ignav; we filter client-side)
    if max_stops is not None and max_stops > 0:
        body["max_stops"] = max(1, min(int(max_stops), 2))
    for key in ("children", "infants_in_seat", "infants_on_lap", "min_carry_on_bags",
                "min_checked_bags", "max_price", "airlines_include", "airlines_exclude"):
        if key in config:
            body[key] = config[key]
    return body


# ── Ignav segment extraction ──────────────────────────────────────────────────

def _extract_ignav_segments(leg: dict) -> tuple[list, int]:
    """Return (list[SegmentInfo], total_duration_min) for one Ignav leg."""
    segments = leg.get("segments") or []
    result = []
    for i, seg in enumerate(segments):
        parts = [seg.get("marketing_carrier_code"), seg.get("flight_number")]
        flight = " ".join(str(x) for x in parts if x)
        dep_local = _time_label(seg.get("departure_time_local") or seg.get("departure_time_utc"))
        arr_local = _time_label(seg.get("arrival_time_local") or seg.get("arrival_time_utc"))
        duration_min = int(seg.get("duration_minutes") or 0)
        aircraft = seg.get("aircraft") or ""

        layover_min = 0
        if i < len(segments) - 1:
            next_seg = segments[i + 1]
            try:
                arr_str = seg.get("arrival_time_utc", "").replace("Z", "+00:00")
                dep_str = next_seg.get("departure_time_utc", "").replace("Z", "+00:00")
                layover_min = int(
                    (datetime.fromisoformat(dep_str) - datetime.fromisoformat(arr_str)).total_seconds() / 60
                )
            except (ValueError, TypeError):
                pass

        result.append(SegmentInfo(
            flight=flight,
            from_airport=seg.get("departure_airport") or "",
            to_airport=seg.get("arrival_airport") or "",
            dep_local=dep_local,
            arr_local=arr_local,
            duration_min=duration_min,
            aircraft=aircraft,
            layover_min=layover_min,
        ))

    total = int(leg.get("duration_minutes") or sum(s.duration_min for s in result))
    return result, total


def _build_ignav_offer(item: dict) -> FlightOffer:
    """Build a rich FlightOffer from a single Ignav itinerary dict."""
    outbound = item["outbound"]
    segments = outbound["segments"]
    first_seg = segments[0]
    airline = outbound.get("carrier") or first_seg.get("marketing_carrier_code") or "Unknown"
    out_segs, out_dur = _extract_ignav_segments(outbound)
    inb_segs, inb_dur = _extract_ignav_segments(item.get("inbound") or {})
    return FlightOffer(
        price=float(item["price"]["amount"]),
        currency=item["price"].get("currency", "USD"),
        departure_date=first_seg["departure_time_local"][:10],
        final_leg_date="",
        stops=max(len(segments) - 1, 0),
        airline=airline,
        details="\n".join(
            part for part in [
                _format_leg("Outbound", item.get("outbound")),
                _format_leg("Inbound", item.get("inbound")),
            ] if part
        ),
        outbound_segments=out_segs,
        inbound_segments=inb_segs,
        outbound_duration_min=out_dur or None,
        inbound_duration_min=inb_dur or None,
    )


def _ignav_cheapest_offer(data: dict, max_stops: int | None = None) -> FlightOffer | None:
    itineraries = data.get("itineraries", [])
    valid = [
        item for item in itineraries
        if item.get("price", {}).get("amount") is not None
        and item.get("outbound", {}).get("segments")
        and item.get("outbound", {}).get("carrier") not in EXCLUDED_AIRLINES
        and (max_stops is None or len(item["outbound"]["segments"]) - 1 <= max_stops)
    ]
    if not valid:
        return None
    return _build_ignav_offer(min(valid, key=lambda item: float(item["price"]["amount"])))


def _ignav_offers_by_stops(data: dict) -> dict:
    """Return {0: nonstop_offer, 1: one_stop_offer} from a single Ignav API response."""
    itineraries = data.get("itineraries", [])
    valid = [
        item for item in itineraries
        if item.get("price", {}).get("amount") is not None
        and item.get("outbound", {}).get("segments")
        and item.get("outbound", {}).get("carrier") not in EXCLUDED_AIRLINES
    ]
    result: dict = {0: None, 1: None}
    for stop_count in (0, 1):
        candidates = [
            item for item in valid
            if len(item["outbound"]["segments"]) - 1 == stop_count
        ]
        if candidates:
            best = min(candidates, key=lambda item: float(item["price"]["amount"]))
            result[stop_count] = _build_ignav_offer(best)
    return result


# ── SerpAPI offer extraction ──────────────────────────────────────────────────

def _serpapi_build_offer(best: dict, stop_count: int) -> FlightOffer:
    flights = best["flights"]
    first_flight = flights[0]
    departure_date = first_flight["departure_airport"]["time"][:10]
    airline = first_flight.get("airline", "Unknown")
    outbound = {
        "carrier": airline,
        "duration_minutes": best.get("total_duration"),
        "segments": [
            {
                "airline": f.get("airline"),
                "departure_airport": f.get("departure_airport", {}),
                "arrival_airport": f.get("arrival_airport", {}),
                "duration": f.get("duration"),
            }
            for f in flights
        ],
    }
    return FlightOffer(
        price=float(best["price"]),
        currency="USD",
        departure_date=departure_date,
        final_leg_date="",
        stops=stop_count,
        airline=airline,
        details=_format_leg("Outbound", outbound),
    )


def _cheapest_offers_by_stops(data: dict) -> dict:
    """Return {0: nonstop_offer, 1: one_stop_offer} from a SerpAPI response."""
    offers = data.get("best_flights", []) + data.get("other_flights", [])
    valid = [
        o for o in offers
        if "price" in o and o.get("flights")
        and o["flights"][0].get("airline") not in EXCLUDED_AIRLINES
    ]
    result: dict = {0: None, 1: None}
    for stop_count in (0, 1):
        candidates = [o for o in valid if len(o.get("layovers", [])) == stop_count]
        if candidates:
            best = min(candidates, key=lambda o: o["price"])
            result[stop_count] = _serpapi_build_offer(best, stop_count)
    return result


# ── Round-trip search ─────────────────────────────────────────────────────────

def search_round_trip_serpapi(route: dict, config: dict, stops_filter: int = 2) -> dict:
    """Return {0: nonstop_offer, 1: one_stop_offer} — cheapest across all date combos."""
    dates = _sample_dates(config["date_start"], config["date_end"], config["sample_dates"])
    date_end = date.fromisoformat(config["date_end"])
    best: dict = {0: None, 1: None}

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
                        "stops": "2",  # fetch all; filter client-side
                    }, cache_hours=float(config.get("cache_hours", 6)))
                    offers = _cheapest_offers_by_stops(data)
                    for stop_count, offer in offers.items():
                        if offer:
                            offer.final_leg_date = str(ret_date)
                            _annotate_destination(offer, destination_name)
                            if best[stop_count] is None or offer.price < best[stop_count].price:
                                best[stop_count] = offer
                except Exception as e:
                    print(f"WARNING [{route['origin']}-{destination} {dep_date}]: {e}")
    return best


def search_round_trip_ignav(route: dict, config: dict, max_stops: int | None = 1) -> dict:
    """Return {0: nonstop_offer, 1: one_stop_offer} from Ignav — single API call per date combo."""
    dates = _sample_dates(config["date_start"], config["date_end"], config["sample_dates"])
    date_end = date.fromisoformat(config["date_end"])
    best: dict = {0: None, 1: None}

    for destination, destination_name in _destination_options(route):
        for dep_date in dates:
            for stay in _stay_options(route):
                ret_date = dep_date + timedelta(days=stay)
                if ret_date > date_end:
                    continue
                try:
                    # No server-side max_stops — fetch all itineraries, split client-side
                    payload = _ignav_request_base(config, None)
                    payload.update({
                        "origin": route["origin"],
                        "destination": destination,
                        "departure_date": str(dep_date),
                        "return_date": str(ret_date),
                    })
                    data = _ignav_post("/fares/round-trip", payload, config)
                    offers = _ignav_offers_by_stops(data)
                    for stop_count, offer in offers.items():
                        if offer:
                            offer.final_leg_date = str(ret_date)
                            _annotate_destination(offer, destination_name)
                            if best[stop_count] is None or offer.price < best[stop_count].price:
                                best[stop_count] = offer
                except Exception as e:
                    print(f"WARNING [Ignav {route['origin']}-{destination} {dep_date}]: {e}")
    return best


def search_round_trip(route: dict, config: dict, stops_filter: int = 2) -> dict:
    if _provider(config) == "ignav":
        return search_round_trip_ignav(route, config)
    return search_round_trip_serpapi(route, config)


# ── Multi-city search ─────────────────────────────────────────────────────────

def _combine_legs(legs: list, final_date: date, stops: int) -> FlightOffer:
    """Merge three one-way FlightOffers into a single multi-city FlightOffer."""
    l1, l2, l3 = legs
    return FlightOffer(
        price=l1.price + l2.price + l3.price,
        currency=l1.currency,
        departure_date=l1.departure_date,
        final_leg_date=str(final_date),
        stops=stops,
        airline=" + ".join([l1.airline, l2.airline, l3.airline]),
        details="\n\n".join(
            part for part in [
                f"Leg 1:\n{l1.details}" if l1.details else "",
                f"Leg 2:\n{l2.details}" if l2.details else "",
                f"Leg 3:\n{l3.details}" if l3.details else "",
            ] if part
        ),
        outbound_segments=l1.outbound_segments,
        inbound_segments=l3.outbound_segments,
        outbound_duration_min=l1.outbound_duration_min,
        inbound_duration_min=l3.outbound_duration_min,
    )


def search_multi_city_serpapi(route: dict, config: dict, stops_filter: int = 2) -> dict:
    dates = _sample_dates(config["date_start"], config["date_end"], config["sample_dates"])
    date_end = date.fromisoformat(config["date_end"])
    segs = route["segments"]
    best: dict = {0: None, 1: None}

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
                        "stops": "2",
                    }, cache_hours=float(config.get("cache_hours", 6)))
                    offers = _cheapest_offers_by_stops(data)
                    for stop_count, offer in offers.items():
                        if offer:
                            offer.final_leg_date = str(ret_date)
                            if best[stop_count] is None or offer.price < best[stop_count].price:
                                best[stop_count] = offer
                except Exception as e:
                    print(f"WARNING [multi-city {dep_date}/{stay1}/{stay2}]: {e}")
    return best


def search_multi_city_ignav(route: dict, config: dict, max_stops: int | None = 1) -> dict:
    dates = _sample_dates(config["date_start"], config["date_end"], config["sample_dates"])
    date_end = date.fromisoformat(config["date_end"])
    segs = route["segments"]
    best: dict = {0: None, 1: None}

    for dep_date in dates:
        for stay1 in _stay_options(segs[0]):
            for stay2 in _stay_options(segs[1]):
                mid_date = dep_date + timedelta(days=stay1)
                ret_date = mid_date + timedelta(days=stay2)
                if ret_date > date_end:
                    continue
                try:
                    # Fetch each leg without server-side max_stops; filter client-side per target
                    payload1 = _ignav_request_base(config, None)
                    payload1.update({"origin": segs[0]["origin"], "destination": segs[0]["destination"],
                                     "departure_date": str(dep_date)})
                    payload2 = _ignav_request_base(config, None)
                    payload2.update({"origin": segs[1]["origin"], "destination": segs[1]["destination"],
                                     "departure_date": str(mid_date)})
                    payload3 = _ignav_request_base(config, None)
                    payload3.update({"origin": segs[2]["origin"], "destination": segs[2]["destination"],
                                     "departure_date": str(ret_date)})
                    data1 = _ignav_post("/fares/one-way", payload1, config)
                    data2 = _ignav_post("/fares/one-way", payload2, config)
                    data3 = _ignav_post("/fares/one-way", payload3, config)

                    for target_stops in (0, 1):
                        l1 = _ignav_cheapest_offer(data1, max_stops=target_stops)
                        l2 = _ignav_cheapest_offer(data2, max_stops=target_stops)
                        l3 = _ignav_cheapest_offer(data3, max_stops=target_stops)
                        if not (l1 and l2 and l3):
                            continue
                        if len({l1.currency, l2.currency, l3.currency}) != 1:
                            continue
                        actual_stops = max(l1.stops, l2.stops, l3.stops)
                        offer = _combine_legs([l1, l2, l3], ret_date, actual_stops)
                        if best[target_stops] is None or offer.price < best[target_stops].price:
                            best[target_stops] = offer
                except Exception as e:
                    print(f"WARNING [Ignav multi-city {dep_date}/{stay1}/{stay2}]: {e}")
    return best


def search_multi_city(route: dict, config: dict, stops_filter: int = 2) -> dict:
    if _provider(config) == "ignav":
        return search_multi_city_ignav(route, config)
    return search_multi_city_serpapi(route, config)
