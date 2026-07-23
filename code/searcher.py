import json
import os
import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from code.browser import launch_browser
from code.gflights import SEAT as _GFLIGHTS_SEAT
from code.gflights_searcher import search_all_options as scrape_search_all_options

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
    # SerpAPI round-trip only: token to fetch the matching return flights in a
    # second call. Empty once the inbound leg has been resolved.
    departure_token: str = ""


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


def _max_total_stay(route: dict) -> int:
    """Longest possible trip length in days (first departure → final return).

    Used so weekday sampling doesn't pick departures whose itinerary can't
    return within the window — the search loops would filter those out anyway.
    """
    if route.get("type") == "multi_city":
        return sum(max(_stay_options(s)) for s in route.get("segments", []) if "stay_min" in s)
    return max(_stay_options(route)) if "stay_min" in route else 0


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


_WEEKDAY_NUM = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _sample_dates(start: str, end: str, n: int, weekdays: list[str] | None = None) -> list[date]:
    d_start = date.fromisoformat(start)
    d_end = date.fromisoformat(end)
    if weekdays:
        # restrict to the given weekdays, then evenly sample n of those dates
        want = {_WEEKDAY_NUM[w[:3].lower()] for w in weekdays}
        span = max((d_end - d_start).days, 0)
        matches = [d for i in range(span + 1)
                   if (d := d_start + timedelta(days=i)).weekday() in want]
        if not matches:
            return [d_start]
        if n <= 1 or n >= len(matches):
            return matches[:1] if n <= 1 else matches
        picks = [matches[round(i * (len(matches) - 1) / (n - 1))] for i in range(n)]
        return list(dict.fromkeys(picks))
    if n <= 1:
        return [d_start]
    span = (d_end - d_start).days
    return [d_start + timedelta(days=round(i * span / (n - 1))) for i in range(n)]


def _route_dates(route: dict, config: dict) -> tuple[list[date], date]:
    """Effective (sampled departure dates, window end) for a route.

    A route may override the global ``search:`` window with its own
    ``date_start`` / ``date_end`` / ``sample_dates`` / ``weekdays``; anything
    unset falls back to the global config. When ``weekdays`` is set, departures
    are sampled only on those weekdays and only early enough that the longest
    itinerary still returns by ``date_end``.
    """
    start = route.get("date_start", config["date_start"])
    end = route.get("date_end", config["date_end"])
    n = int(route.get("sample_dates", config["sample_dates"]))
    end_date = date.fromisoformat(end)
    weekdays = route.get("weekdays") or config.get("weekdays")
    dep_end = end
    if weekdays:
        latest = end_date - timedelta(days=_max_total_stay(route))
        dep_end = max(latest, date.fromisoformat(start)).isoformat()
    return _sample_dates(start, dep_end, n, weekdays), end_date


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
    airport = segment.get(f"{side}_airport")
    if isinstance(airport, dict):
        return airport.get("id") or ""
    return airport or segment.get(f"{side}_airport_code") or ""


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

def _serpapi_leg_dict(flights: list, total_duration) -> dict:
    first_flight = flights[0]
    airline = first_flight.get("airline", "Unknown")
    return {
        "carrier": airline,
        "duration_minutes": total_duration,
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


def _serpapi_build_offer(best: dict, stop_count: int) -> FlightOffer:
    flights = best["flights"]
    first_flight = flights[0]
    departure_date = first_flight["departure_airport"]["time"][:10]
    airline = first_flight.get("airline", "Unknown")
    outbound = _serpapi_leg_dict(flights, best.get("total_duration"))
    return FlightOffer(
        price=float(best["price"]),
        currency="USD",
        departure_date=departure_date,
        final_leg_date="",
        stops=stop_count,
        airline=airline,
        details=_format_leg("Outbound", outbound),
        departure_token=best.get("departure_token", ""),
    )


def _serpapi_fetch_inbound(base_params: dict, departure_token: str, cache_hours: float) -> dict | None:
    """Second-step SerpAPI call: resolve the return flights for a chosen outbound.

    Google Flights' round-trip search is two calls — the first returns outbound
    options with a ``departure_token`` each; passing that token back (same
    origin/destination/dates) returns the matching return flights and the
    final total price for that specific combination.
    """
    params = dict(base_params)
    params["departure_token"] = departure_token
    data = _search(params, cache_hours=cache_hours)
    candidates = data.get("best_flights", []) + data.get("other_flights", [])
    valid = [o for o in candidates if "price" in o and o.get("flights")]
    if not valid:
        return None
    return min(valid, key=lambda o: o["price"])


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


# ── Scrape offer extraction (Google Flights via code.gflights_searcher) ──────

def _scrape_offers_by_stops(options: list) -> dict:
    """{0: cheapest nonstop option, 1: cheapest exactly-1-stop option} from an
    already-parsed options list (code.gflights_searcher.parse_all_options).
    Matches the exact-stop-count binning `_cheapest_offers_by_stops`/
    `_ignav_offers_by_stops` already use — 2+-stop options are dropped, same
    as those providers do client-side."""
    result: dict = {0: None, 1: None}
    for stop_count, label in ((0, "Nonstop"), (1, "1 stop")):
        candidates = [o for o in options if o["stops"] == label]
        if candidates:
            result[stop_count] = min(candidates, key=lambda o: o["price"])
    return result


def _scrape_build_offer(option: dict, departure_date: str, final_leg_date: str) -> FlightOffer:
    """Build a FlightOffer from one parsed scrape option.

    Scraping only exposes total price + the FIRST leg's detail (airline,
    stops, duration, one layover) — not full per-direction segment detail the
    way SerpAPI/Ignav give it. `details` is therefore a single summary line,
    not the Outbound:/Inbound: structured text those providers produce; this
    is an accepted trade-off — see
    docs/superpowers/specs/2026-07-23-scraping-rewrite-design.md.
    """
    stops = 0 if option["stops"] == "Nonstop" else int(option["stops"].split()[0])
    duration = _duration_label(option.get("duration_min"))
    detail = (
        f"{option['stops']} flight with {option['airline']}"
        + (f", {duration}" if duration else "")
        + f"\n  {option['dep_airport']} {option['dep_time']} -> "
          f"{option['arr_airport']} {option['arr_time']}"
    )
    if option.get("layover_airport"):
        lay_dur = _duration_label(option.get("layover_min"))
        detail += f"\n  layover{f' ({lay_dur})' if lay_dur else ''} at {option['layover_airport']}"
    return FlightOffer(
        price=option["price"],
        currency=option.get("currency", "USD"),
        departure_date=departure_date,
        final_leg_date=final_leg_date,
        stops=stops,
        airline=option["airline"],
        details=detail,
    )


def search_round_trip_scrape(route: dict, config: dict) -> dict:
    """Return {0: nonstop_offer, 1: one_stop_offer} for a round trip, scraped
    directly off Google Flights — no API, no quota."""
    dates, date_end = _route_dates(route, config)
    seat = _GFLIGHTS_SEAT.get(str(config.get("cabin_class", "economy")).lower(), 1)
    adults = int(config.get("adults", 1))
    best: dict = {0: None, 1: None}

    pw, browser, page = launch_browser()
    try:
        for destination, destination_name in _destination_options(route):
            for dep_date in dates:
                for stay in _stay_options(route):
                    ret_date = dep_date + timedelta(days=stay)
                    if ret_date > date_end:
                        continue
                    try:
                        legs = [
                            (route["origin"], destination, str(dep_date)),
                            (destination, route["origin"], str(ret_date)),
                        ]
                        options = scrape_search_all_options(page, legs, seat, adults)
                        offers = _scrape_offers_by_stops(options)
                        for stop_count, option in offers.items():
                            if option is None:
                                continue
                            offer = _scrape_build_offer(option, str(dep_date), str(ret_date))
                            _annotate_destination(offer, destination_name)
                            if best[stop_count] is None or offer.price < best[stop_count].price:
                                best[stop_count] = offer
                    except Exception as e:
                        print(f"WARNING [scrape {route['origin']}-{destination} {dep_date}]: {e}")
    finally:
        browser.close(); pw.stop()
    return best


def search_multi_city_scrape(route: dict, config: dict) -> dict:
    """Return {0: nonstop_offer, 1: one_stop_offer} for a multi-city trip,
    scraped directly off Google Flights — no API, no quota."""
    dates, date_end = _route_dates(route, config)
    segs = route["segments"]
    seat = _GFLIGHTS_SEAT.get(str(config.get("cabin_class", "economy")).lower(), 1)
    adults = int(config.get("adults", 1))
    best: dict = {0: None, 1: None}

    pw, browser, page = launch_browser()
    try:
        for dep_date in dates:
            for stay1 in _stay_options(segs[0]):
                for stay2 in _stay_options(segs[1]):
                    mid_date = dep_date + timedelta(days=stay1)
                    ret_date = mid_date + timedelta(days=stay2)
                    if ret_date > date_end:
                        continue
                    try:
                        legs = [
                            (segs[0]["origin"], segs[0]["destination"], str(dep_date)),
                            (segs[1]["origin"], segs[1]["destination"], str(mid_date)),
                            (segs[2]["origin"], segs[2]["destination"], str(ret_date)),
                        ]
                        options = scrape_search_all_options(page, legs, seat, adults)
                        offers = _scrape_offers_by_stops(options)
                        for stop_count, option in offers.items():
                            if option is None:
                                continue
                            offer = _scrape_build_offer(option, str(dep_date), str(ret_date))
                            if best[stop_count] is None or offer.price < best[stop_count].price:
                                best[stop_count] = offer
                    except Exception as e:
                        print(f"WARNING [scrape multi-city {dep_date}/{stay1}/{stay2}]: {e}")
    finally:
        browser.close(); pw.stop()
    return best


# ── Round-trip search ─────────────────────────────────────────────────────────

def search_round_trip_serpapi(route: dict, config: dict, stops_filter: int = 2) -> dict:
    """Return {0: nonstop_offer, 1: one_stop_offer} — cheapest across all date combos.

    ``route["airline_filter"]`` (IATA code, e.g. "HU") restricts results to a
    single carrier. Not currently used by any active route — was added for
    Hainan's BOS-PEK service (config/routes.yaml, retired 2026-07-22: Ignav
    turned out to cover that fare too, on the same provider as the general
    BOS-China route) — kept as a generic capability for any future
    single-carrier-preference route.
    """
    dates, date_end = _route_dates(route, config)
    best: dict = {0: None, 1: None}
    airline_filter = route.get("airline_filter")

    for destination, destination_name in _destination_options(route):
        for dep_date in dates:
            for stay in _stay_options(route):
                ret_date = dep_date + timedelta(days=stay)
                if ret_date > date_end:
                    continue
                try:
                    params = {
                        "type": "1",
                        "departure_id": route["origin"],
                        "arrival_id": destination,
                        "outbound_date": str(dep_date),
                        "return_date": str(ret_date),
                        "stops": "2",  # fetch all; filter client-side
                    }
                    if airline_filter:
                        params["include_airlines"] = airline_filter
                    cache_hours = float(config.get("cache_hours", 6))
                    data = _search(params, cache_hours=cache_hours)
                    offers = _cheapest_offers_by_stops(data)
                    for stop_count, offer in offers.items():
                        if offer:
                            offer.final_leg_date = str(ret_date)
                            _annotate_destination(offer, destination_name)
                            if offer.departure_token:
                                inbound_best = _serpapi_fetch_inbound(
                                    params, offer.departure_token, cache_hours
                                )
                                if inbound_best:
                                    inbound_leg = _serpapi_leg_dict(
                                        inbound_best["flights"], inbound_best.get("total_duration")
                                    )
                                    offer.details = "\n".join(
                                        part for part in
                                        [offer.details, _format_leg("Inbound", inbound_leg)]
                                        if part
                                    )
                                    offer.price = float(inbound_best["price"])
                                offer.departure_token = ""
                            if best[stop_count] is None or offer.price < best[stop_count].price:
                                best[stop_count] = offer
                except Exception as e:
                    print(f"WARNING [{route['origin']}-{destination} {dep_date}]: {e}")
    return best


def search_round_trip_ignav(route: dict, config: dict, max_stops: int | None = 1) -> dict:
    """Return {0: nonstop_offer, 1: one_stop_offer} from Ignav — single API call per date combo."""
    dates, date_end = _route_dates(route, config)
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
    provider = _provider(config)
    if provider == "ignav":
        return search_round_trip_ignav(route, config)
    if provider == "serpapi":
        return search_round_trip_serpapi(route, config)
    return search_round_trip_scrape(route, config)


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
    dates, date_end = _route_dates(route, config)
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
    dates, date_end = _route_dates(route, config)
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
    provider = _provider(config)
    if provider == "ignav":
        return search_multi_city_ignav(route, config)
    if provider == "serpapi":
        return search_multi_city_serpapi(route, config)
    return search_multi_city_scrape(route, config)
