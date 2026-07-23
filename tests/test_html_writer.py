from code.html_writer import _airport_codes_from_details, _latest_per_pair

ROUTE_CFG_ROUND_TRIP = {"origin": "BOS", "destination": "PEK"}
ROUTE_CFG_MULTI_CITY = {"segments": [{"origin": "BOS", "destination": "NRT"}]}


def test_airport_codes_legacy_serpapi_format_with_connection():
    # Old SerpAPI/Ignav format: outbound has its own connection (LAX), then
    # "Inbound:" starts a genuinely different (return) direction. The last
    # code BEFORE "Inbound:" is the true outbound destination (PEK), even
    # though the outbound itself connects through LAX.
    details = (
        "Outbound: United, 1 stop(s), 28h25m\n"
        "  BOS 2026-09-01 12:15 -> LAX 2026-09-01 15:30 (UA 659)\n"
        "  LAX 2026-09-01 23:35 -> PEK 2026-09-03 04:40 (UA 771)\n"
        "Inbound: Air Canada, 2 stop(s), 27h05m\n"
        "  PEK 2026-09-22 18:45 -> YVR 2026-09-22 14:25 (AC 30)"
    )
    origin, dest = _airport_codes_from_details(details, ROUTE_CFG_ROUND_TRIP)
    assert origin == "BOS"
    assert dest == "PEK"


def test_airport_codes_new_scrape_multicity_format_ignores_later_legs():
    # New scrape format: Leg 1 is BOS->NRT; Leg 2/3 (KIX->SHA, HKG->BOS) are a
    # totally different direction/city, not a continuation of leg 1. The
    # search link should describe leg 1 only (BOS->NRT), not leak the trip's
    # final return-home airport (BOS again) as the "destination".
    details = (
        "Leg 1: BOS→NRT (2026-11-24) — Nonstop with JAL, 14h 10m\n"
        "  Boston Logan International Airport 12:05 PM -> Narita International Airport 4:15 PM\n"
        "Leg 2: KIX→SHA (2026-12-04)\n"
        "Leg 3: HKG→BOS (2026-12-22)"
    )
    origin, dest = _airport_codes_from_details(details, ROUTE_CFG_MULTI_CITY)
    assert origin == "BOS"
    assert dest == "NRT"


def test_airport_codes_new_scrape_round_trip_format():
    details = (
        "Leg 1: BOS→PEK (2026-10-28) — Nonstop with Hainan, 16h 35m\n"
        "  Boston Logan International Airport 11:55 PM -> Beijing Capital International Airport 4:30 AM\n"
        "Leg 2: PEK→BOS (2026-11-18)"
    )
    origin, dest = _airport_codes_from_details(details, ROUTE_CFG_ROUND_TRIP)
    assert origin == "BOS"
    assert dest == "PEK"


def test_airport_codes_falls_back_to_route_config_when_no_details():
    origin, dest = _airport_codes_from_details("", ROUTE_CFG_ROUND_TRIP)
    assert origin == "BOS"
    assert dest == ""

    origin, dest = _airport_codes_from_details("", ROUTE_CFG_MULTI_CITY)
    assert origin == "BOS"
    assert dest == ""


PAIR_ROWS = [
    {"ts": "2026-07-20T09:00:00", "dep_date": "2026-10-06", "mid_date": "", "ret_date": "2026-10-27",
     "price": 2200.0, "stops": 0, "airline": "JAL", "details": ""},
    {"ts": "2026-07-23T09:00:00", "dep_date": "2026-10-06", "mid_date": "", "ret_date": "2026-10-27",
     "price": 2071.0, "stops": 0, "airline": "JAL", "details": ""},
    {"ts": "2026-07-21T09:00:00", "dep_date": "2026-10-08", "mid_date": "", "ret_date": "2026-10-29",
     "price": 1900.0, "stops": 1, "airline": "United", "details": ""},
]


def test_latest_per_pair_keeps_most_recent_not_lowest_price():
    current = _latest_per_pair(PAIR_ROWS, stops=0)
    assert len(current) == 1
    assert current[0]["price"] == 2071.0  # the 09-23 observation, not the cheaper 09-20 one


def test_latest_per_pair_filters_by_stop_count():
    current = _latest_per_pair(PAIR_ROWS, stops=1)
    assert len(current) == 1
    assert current[0]["airline"] == "United"


def test_latest_per_pair_sorted_cheapest_first():
    current = _latest_per_pair(PAIR_ROWS, stops=0) + _latest_per_pair(PAIR_ROWS, stops=1)
    # only one row per stop count in this fixture, but confirm the helper
    # doesn't error when called across both and each bucket is internally sorted
    all_rows = _latest_per_pair(PAIR_ROWS + [
        {"ts": "2026-07-23T10:00:00", "dep_date": "2026-10-13", "mid_date": "", "ret_date": "2026-11-03",
         "price": 1480.0, "stops": 0, "airline": "WestJet", "details": ""},
    ], stops=0)
    prices = [r["price"] for r in all_rows]
    assert prices == sorted(prices)
