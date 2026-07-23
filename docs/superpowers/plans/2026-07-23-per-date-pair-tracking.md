# Per-Date-Pair Tracking + Trip Detail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every sampled Oct–Dec date combination's price (not just the single overall-cheapest), and show full leg detail everywhere a trip is displayed, instead of blank Outbound/Inbound/Total columns.

**Architecture:** `search_multi_city_scrape`/`search_round_trip_scrape` already loop over every date combination and discard everything but the winner — change them to also return every combo's result. Persist those to two new CSVs (one per route type, matching the existing type-split convention). Add a new "All Sampled Dates" table to the dashboard, one row per date combo, and replace the existing history table's blank Outbound/Inbound/Total columns with the leg-detail text that's already being generated and stored.

**Tech Stack:** Python, existing `code/searcher.py`/`code/notifier.py`/`code/html_writer.py`/`main.py` structure, pytest.

## Global Constraints

- Every task must leave `pytest` green before moving to the next task.
- The existing cheapest-overall + rolling-average alert system
  (`data/prices.csv`, `data/round_trip_prices.csv`, `code/analyzer.py`,
  the top summary banner in each route card) is **not changed** — same
  files, same columns, same alert logic, same notifications.
- SerpAPI/Ignav's search functions (`search_round_trip_serpapi`,
  `search_round_trip_ignav`, `search_multi_city_serpapi`,
  `search_multi_city_ignav`) get **only** a one-line return-shape change
  (`return best` → `return best, []`) — their search logic is untouched, and
  they do not persist per-combo data (out of scope per the spec).
- Scope: scrape provider only, both routes (Asia Grand Tour = 3 date columns,
  BOS ↔ Beijing = 2 date columns).

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `code/searcher.py` | Modified | `search_round_trip_scrape`/`search_multi_city_scrape` return `(best, all_results)` instead of `best`; the 4 fallback-provider functions return `(best, [])`. |
| `code/notifier.py` | Modified | New `append_pair_rows(csv_path, route_name, pair_results, is_multi_city)` writer. |
| `main.py` | Modified | Unpacks the new tuple; new CSV path constants; calls the new writer; passes pair-CSV paths through to `write_html`. |
| `code/html_writer.py` | Modified | Fix `_airport_codes_from_details` to slice to leg 1 only (handles the new multi-leg format correctly); `_history_table`'s Outbound/Inbound/Total columns → one `Details` column; new `_load_pair_history`/`_latest_per_pair`/`_pair_table` functions render the new "All Sampled Dates" table; delete now-unused `_parse_legs`/`_leg_summary`. |
| `tests/test_searcher.py` | Modified | Update the 3 existing scrape-provider tests for the new return shape; add 2 new tests asserting `all_results` actually contains one entry per sampled combo. |
| `tests/test_notifier.py` | Modified | New tests for `append_pair_rows` (both 2-date and 3-date shapes). |
| `tests/test_html_writer.py` | New | Flight-bot has no existing html_writer tests — new file covering `_airport_codes_from_details`'s leg-1 slicing (old and new detail formats) and `_latest_per_pair`'s dedup/sort logic. |

---

### Task 1: Capture every sampled date combo's result in the scrape search functions

**Files:**
- Modify: `code/searcher.py`
- Test: `tests/test_searcher.py`

**Interfaces:**
- Produces: `search_round_trip_scrape(route, config) -> tuple[dict, list[dict]]`,
  `search_multi_city_scrape(route, config) -> tuple[dict, list[dict]]`. Each
  `all_results` entry: `{"dates": list[str], "stops": int, "offer": FlightOffer}`
  — `dates` has 2 elements for round-trip, 3 for multi-city.
- Also changes (one-line each): `search_round_trip_serpapi`,
  `search_round_trip_ignav`, `search_multi_city_serpapi`,
  `search_multi_city_ignav` now `return best, []` instead of `return best`.
  `search_round_trip`/`search_multi_city` (the dispatchers) need no changes —
  they already just `return search_round_trip_X(route, config)`, which now
  returns a tuple regardless of branch.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_searcher.py`. First, update the three existing scrape
tests to unpack the new tuple (find and replace these three blocks with the
versions below — the fixture data and assertions on `result[...]` are
unchanged, only the call site changes from `result = search_round_trip(...)`
to `result, pairs = search_round_trip(...)`):

```python
def test_provider_defaults_to_serpapi_when_unset(monkeypatch):
    monkeypatch.delenv("FLIGHT_PROVIDER", raising=False)
    from code.searcher import _provider
    assert _provider({}) == "serpapi"


def test_search_round_trip_scrape_returns_nonstop_and_onestop(monkeypatch):
    monkeypatch.setattr("code.browser.launch_browser", lambda: (MagicMock(), MagicMock(), MagicMock()))

    def fake_search_all_options(page, legs, seat, adults):
        return [
            {"price": 944.0, "currency": "USD", "airline": "Hainan", "stops": "Nonstop",
             "dep_airport": "BOS", "dep_time": "11:55 PM", "dep_date": "Wed, Sep 30",
             "arr_airport": "PEK", "arr_time": "4:30 AM", "arr_date": "Fri, Oct 2",
             "duration_min": 995, "layover_min": None, "layover_airport": None},
            {"price": 780.0, "currency": "USD", "airline": "United", "stops": "1 stop",
             "dep_airport": "BOS", "dep_time": "12:15 PM", "dep_date": "Wed, Sep 30",
             "arr_airport": "PEK", "arr_time": "4:40 AM", "arr_date": "Fri, Oct 2",
             "duration_min": 1705, "layover_min": 125, "layover_airport": "LAX"},
        ]
    monkeypatch.setattr("code.searcher.scrape_search_all_options", fake_search_all_options)

    route = {"origin": "BOS", "destination": "PEK", "stay_min": 21, "stay_max": 21}
    config = {"date_start": "2026-10-06", "date_end": "2026-12-01", "sample_dates": 1, "provider": "scrape"}
    result, pairs = search_round_trip(route, config)

    assert result[0].price == 944.0
    assert result[0].airline == "Hainan"
    assert result[0].departure_date == "2026-10-06"
    assert result[1].price == 780.0
    assert result[1].airline == "United"
    assert "Hainan" in result[0].details
    assert pairs  # non-empty: at least this one date combo was captured
    assert all(set(p.keys()) == {"dates", "stops", "offer"} for p in pairs)
    assert all(len(p["dates"]) == 2 for p in pairs)


def test_search_round_trip_scrape_captures_every_sampled_combo(monkeypatch):
    """sample_dates=1 with a fixed 21-day stay means exactly one date combo is
    searched -- pairs must have exactly one entry per stop count found."""
    monkeypatch.setattr("code.browser.launch_browser", lambda: (MagicMock(), MagicMock(), MagicMock()))

    def fake_search_all_options(page, legs, seat, adults):
        return [
            {"price": 944.0, "currency": "USD", "airline": "Hainan", "stops": "Nonstop",
             "dep_airport": "BOS", "dep_time": "11:55 PM", "dep_date": "Wed, Sep 30",
             "arr_airport": "PEK", "arr_time": "4:30 AM", "arr_date": "Fri, Oct 2",
             "duration_min": 995, "layover_min": None, "layover_airport": None},
        ]
    monkeypatch.setattr("code.searcher.scrape_search_all_options", fake_search_all_options)

    route = {"origin": "BOS", "destination": "PEK", "stay_min": 21, "stay_max": 21}
    config = {"date_start": "2026-10-06", "date_end": "2026-10-06", "sample_dates": 1, "provider": "scrape"}
    result, pairs = search_round_trip(route, config)

    assert len(pairs) == 1
    assert pairs[0]["stops"] == 0
    assert pairs[0]["dates"] == ["2026-10-06", "2026-10-27"]
    assert pairs[0]["offer"].price == 944.0


def test_search_multi_city_scrape_returns_nonstop_and_onestop(monkeypatch):
    monkeypatch.setattr("code.browser.launch_browser", lambda: (MagicMock(), MagicMock(), MagicMock()))

    def fake_search_all_options(page, legs, seat, adults):
        return [
            {"price": 2071.0, "currency": "USD", "airline": "JAL", "stops": "Nonstop",
             "dep_airport": "BOS", "dep_time": "1:00 PM", "dep_date": "Tue, Oct 13",
             "arr_airport": "NRT", "arr_time": "4:00 PM", "arr_date": "Wed, Oct 14",
             "duration_min": 840, "layover_min": None, "layover_airport": None},
            {"price": 1259.0, "currency": "USD", "airline": "Air Canada", "stops": "1 stop",
             "dep_airport": "BOS", "dep_time": "10:40 AM", "dep_date": "Tue, Oct 13",
             "arr_airport": "NRT", "arr_time": "3:25 PM", "arr_date": "Wed, Oct 14",
             "duration_min": 945, "layover_min": 50, "layover_airport": "Montreal"},
        ]
    monkeypatch.setattr("code.searcher.scrape_search_all_options", fake_search_all_options)

    route = {
        "segments": [
            {"origin": "BOS", "destination": "NRT", "stay_min": 7, "stay_max": 10},
            {"origin": "KIX", "destination": "SHA", "stay_min": 14, "stay_max": 18},
            {"origin": "HKG", "destination": "BOS"},
        ],
    }
    config = {"date_start": "2026-10-06", "date_end": "2026-12-29", "sample_dates": 1, "provider": "scrape"}
    result, pairs = search_multi_city(route, config)

    assert result[0].price == 2071.0
    assert result[0].airline == "JAL"
    assert result[1].price == 1259.0
    assert result[1].airline == "Air Canada"
    assert pairs
    assert all(len(p["dates"]) == 3 for p in pairs)


def test_search_multi_city_scrape_captures_every_sampled_combo(monkeypatch):
    """sample_dates=1 with fixed stay_min==stay_max on both segments means
    exactly one date combo is searched."""
    monkeypatch.setattr("code.browser.launch_browser", lambda: (MagicMock(), MagicMock(), MagicMock()))

    def fake_search_all_options(page, legs, seat, adults):
        return [
            {"price": 2071.0, "currency": "USD", "airline": "JAL", "stops": "Nonstop",
             "dep_airport": "BOS", "dep_time": "1:00 PM", "dep_date": "Tue, Oct 13",
             "arr_airport": "NRT", "arr_time": "4:00 PM", "arr_date": "Wed, Oct 14",
             "duration_min": 840, "layover_min": None, "layover_airport": None},
        ]
    monkeypatch.setattr("code.searcher.scrape_search_all_options", fake_search_all_options)

    route = {
        "segments": [
            {"origin": "BOS", "destination": "NRT", "stay_min": 8, "stay_max": 8},
            {"origin": "KIX", "destination": "SHA", "stay_min": 14, "stay_max": 14},
            {"origin": "HKG", "destination": "BOS"},
        ],
    }
    config = {"date_start": "2026-10-13", "date_end": "2026-11-04", "sample_dates": 1, "provider": "scrape"}
    result, pairs = search_multi_city(route, config)

    assert len(pairs) == 1
    assert pairs[0]["stops"] == 0
    assert pairs[0]["dates"] == ["2026-10-13", "2026-10-21", "2026-11-04"]


def test_search_round_trip_scrape_excludes_turkish_airlines(monkeypatch):
    monkeypatch.setattr("code.browser.launch_browser", lambda: (MagicMock(), MagicMock(), MagicMock()))

    def fake_search_all_options(page, legs, seat, adults):
        return [
            {"price": 944.0, "currency": "USD", "airline": "Hainan", "stops": "Nonstop",
             "dep_airport": "BOS", "dep_time": "11:55 PM", "dep_date": "Wed, Sep 30",
             "arr_airport": "PEK", "arr_time": "4:30 AM", "arr_date": "Fri, Oct 2",
             "duration_min": 995, "layover_min": None, "layover_airport": None},
            {"price": 500.0, "currency": "USD", "airline": "Turkish Airlines", "stops": "1 stop",
             "dep_airport": "BOS", "dep_time": "9:00 PM", "dep_date": "Wed, Sep 30",
             "arr_airport": "PEK", "arr_time": "7:00 AM", "arr_date": "Fri, Oct 2",
             "duration_min": 1320, "layover_min": 225, "layover_airport": "Istanbul"},
            {"price": 780.0, "currency": "USD", "airline": "United", "stops": "1 stop",
             "dep_airport": "BOS", "dep_time": "12:15 PM", "dep_date": "Wed, Sep 30",
             "arr_airport": "PEK", "arr_time": "4:40 AM", "arr_date": "Fri, Oct 2",
             "duration_min": 1705, "layover_min": 125, "layover_airport": "LAX"},
        ]
    monkeypatch.setattr("code.searcher.scrape_search_all_options", fake_search_all_options)

    route = {"origin": "BOS", "destination": "PEK", "stay_min": 21, "stay_max": 21}
    config = {"date_start": "2026-10-06", "date_end": "2026-12-01", "sample_dates": 1, "provider": "scrape"}
    result, pairs = search_round_trip(route, config)

    assert result[1].price == 780.0
    assert "Turkish Airlines" not in (result[1].details or "")
```

**IMPORTANT:** the current file has these three existing scrape tests calling
`monkeypatch.setattr("code.searcher.launch_browser", ...)` — but a prior fix
moved `launch_browser` to a *local* import inside the two `_scrape` functions,
so the correct monkeypatch target is `code.browser.launch_browser` (verify
this matches what's already in the file before assuming — if the file
already says `code.browser.launch_browser`, leave it; only change what's
actually stale). Search for `monkeypatch.setattr("code.searcher.launch_browser"`
in the current file first — if any hits remain, that's what needs fixing.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/projects/claude/flight-bot && source .venv/bin/activate && python -m pytest tests/test_searcher.py -k scrape -v`
Expected: FAIL — `ValueError: too many values to unpack` or similar (functions still return a bare dict, not a tuple)

- [ ] **Step 3: Modify `code/searcher.py`**

Change `search_round_trip_scrape` (currently ends with `return best` at the
line right after `finally: browser.close(); pw.stop()`):

```python
def search_round_trip_scrape(route: dict, config: dict) -> tuple[dict, list[dict]]:
    """Return (best, all_results) for a round trip, scraped directly off
    Google Flights — no API, no quota.

    best: {0: nonstop_offer, 1: one_stop_offer} -- unchanged, feeds the
    existing alert/notifier flow. all_results: one entry per (date-combo,
    stop-count) that returned a fare -- {"dates": [dep_date_str, ret_date_str],
    "stops": 0|1, "offer": FlightOffer} -- feeds the new per-date-pair CSV.
    """
    from code.browser import launch_browser

    dates, date_end = _route_dates(route, config)
    seat = _GFLIGHTS_SEAT.get(str(config.get("cabin_class", "economy")).lower(), 1)
    adults = int(config.get("adults", 1))
    best: dict = {0: None, 1: None}
    all_results: list = []

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
                            offer = _scrape_build_offer(option, legs)
                            _annotate_destination(offer, destination_name)
                            all_results.append({
                                "dates": [str(dep_date), str(ret_date)],
                                "stops": stop_count,
                                "offer": offer,
                            })
                            if best[stop_count] is None or offer.price < best[stop_count].price:
                                best[stop_count] = offer
                    except Exception as e:
                        print(f"WARNING [scrape {route['origin']}-{destination} {dep_date}]: {e}")
    finally:
        browser.close(); pw.stop()
    return best, all_results
```

Change `search_multi_city_scrape` the same way:

```python
def search_multi_city_scrape(route: dict, config: dict) -> tuple[dict, list[dict]]:
    """Return (best, all_results) for a multi-city trip, scraped directly off
    Google Flights — no API, no quota.

    best: {0: nonstop_offer, 1: one_stop_offer} -- unchanged, feeds the
    existing alert/notifier flow. all_results: one entry per (date-combo,
    stop-count) that returned a fare -- {"dates": [dep_date_str, mid_date_str,
    ret_date_str], "stops": 0|1, "offer": FlightOffer} -- feeds the new
    per-date-pair CSV.
    """
    from code.browser import launch_browser

    dates, date_end = _route_dates(route, config)
    segs = route["segments"]
    seat = _GFLIGHTS_SEAT.get(str(config.get("cabin_class", "economy")).lower(), 1)
    adults = int(config.get("adults", 1))
    best: dict = {0: None, 1: None}
    all_results: list = []

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
                            offer = _scrape_build_offer(option, legs)
                            all_results.append({
                                "dates": [str(dep_date), str(mid_date), str(ret_date)],
                                "stops": stop_count,
                                "offer": offer,
                            })
                            if best[stop_count] is None or offer.price < best[stop_count].price:
                                best[stop_count] = offer
                    except Exception as e:
                        print(f"WARNING [scrape multi-city {dep_date}/{stay1}/{stay2}]: {e}")
    finally:
        browser.close(); pw.stop()
    return best, all_results
```

Then change these four `return best` statements (search for them — they are
the last line of each of these four functions) to `return best, []`:
`search_round_trip_serpapi`, `search_round_trip_ignav`,
`search_multi_city_serpapi`, `search_multi_city_ignav`.

`search_round_trip`/`search_multi_city` (the dispatchers) need **no changes**
— `return search_round_trip_ignav(route, config)` etc. already just forward
whatever the branch returns, which is now a tuple in every branch.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_searcher.py -v`
Expected: all pass. (No known pre-existing unrelated failures should appear
here — that flaky cache-dependent test only affects
`test_search_multi_city_returns_cheapest`, which is unrelated to this task
and untouched by it.)

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest -q`
Expected: all green, or only the one pre-existing `test_search_multi_city_returns_cheapest`
cache-flake failure if a stale `data/api_cache/` entry happens to exist —
confirm no *other* failures.

- [ ] **Step 6: Commit**

```bash
git add code/searcher.py tests/test_searcher.py
git commit -m "searcher.py: capture every sampled date combo's result, not just the winner"
```

---

### Task 2: New CSV writer for per-date-pair rows

**Files:**
- Modify: `code/notifier.py`
- Test: `tests/test_notifier.py`

**Interfaces:**
- Consumes: `code.searcher.FlightOffer` (existing dataclass)
- Produces: `append_pair_rows(csv_path: Path, route_name: str, pair_results: list[dict], is_multi_city: bool) -> None`
  — `pair_results` is the `all_results` list Task 1 produces:
  `[{"dates": list[str], "stops": int, "offer": FlightOffer}, ...]`.

- [ ] **Step 1: Write the failing tests**

Read the existing `tests/test_notifier.py` first to match its style, then
add:

```python
def test_append_pair_rows_round_trip_writes_two_date_columns(tmp_path):
    from code.notifier import append_pair_rows
    from code.searcher import FlightOffer

    csv_path = tmp_path / "pairs.csv"
    offer = FlightOffer(
        price=944.0, currency="USD", departure_date="2026-10-06",
        final_leg_date="2026-10-27", stops=0, airline="Hainan",
        details="Leg 1: BOS→PEK (2026-10-06) — Nonstop with Hainan, 16h35m",
    )
    append_pair_rows(csv_path, "Boston-China 3-week",
                      [{"dates": ["2026-10-06", "2026-10-27"], "stops": 0, "offer": offer}],
                      is_multi_city=False)

    import csv
    rows = list(csv.DictReader(open(csv_path)))
    assert len(rows) == 1
    assert rows[0]["route"] == "Boston-China 3-week"
    assert rows[0]["dep_date"] == "2026-10-06"
    assert rows[0]["ret_date"] == "2026-10-27"
    assert "mid_date" not in rows[0]
    assert float(rows[0]["price"]) == 944.0
    assert rows[0]["airline"] == "Hainan"
    assert "Hainan" in rows[0]["details"]


def test_append_pair_rows_multi_city_writes_three_date_columns(tmp_path):
    from code.notifier import append_pair_rows
    from code.searcher import FlightOffer

    csv_path = tmp_path / "pairs.csv"
    offer = FlightOffer(
        price=2071.0, currency="USD", departure_date="2026-10-13",
        final_leg_date="2026-11-04", stops=0, airline="JAL",
        details="Leg 1: BOS→NRT (2026-10-13) — Nonstop with JAL, 14h00m",
    )
    append_pair_rows(csv_path, "Asia Grand Tour",
                      [{"dates": ["2026-10-13", "2026-10-21", "2026-11-04"], "stops": 0, "offer": offer}],
                      is_multi_city=True)

    import csv
    rows = list(csv.DictReader(open(csv_path)))
    assert len(rows) == 1
    assert rows[0]["dep_date"] == "2026-10-13"
    assert rows[0]["mid_date"] == "2026-10-21"
    assert rows[0]["ret_date"] == "2026-11-04"


def test_append_pair_rows_appends_without_rewriting_header(tmp_path):
    from code.notifier import append_pair_rows
    from code.searcher import FlightOffer

    csv_path = tmp_path / "pairs.csv"
    offer = FlightOffer(price=1.0, currency="USD", departure_date="2026-10-06",
                         final_leg_date="2026-10-27", stops=0, airline="X", details="")
    append_pair_rows(csv_path, "R", [{"dates": ["2026-10-06", "2026-10-27"], "stops": 0, "offer": offer}], False)
    append_pair_rows(csv_path, "R", [{"dates": ["2026-10-08", "2026-10-29"], "stops": 1, "offer": offer}], False)

    lines = csv_path.read_text().splitlines()
    assert lines[0].startswith("ts,route,dep_date,ret_date,stops,price,currency,airline,details")
    assert len(lines) == 3  # header + 2 rows, no duplicate header
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_notifier.py -k pair -v`
Expected: FAIL — `ImportError: cannot import name 'append_pair_rows'`

- [ ] **Step 3: Add to `code/notifier.py`**

Add these constants right after the existing `_CSV_FIELDS` definition, and
the new function after `append_to_csv`:

```python
_PAIR_CSV_FIELDS_ROUND_TRIP = [
    "ts", "route", "dep_date", "ret_date", "stops", "price", "currency", "airline", "details",
]
_PAIR_CSV_FIELDS_MULTI_CITY = [
    "ts", "route", "dep_date", "mid_date", "ret_date", "stops", "price", "currency", "airline", "details",
]


def append_pair_rows(
    csv_path: Path,
    route_name: str,
    pair_results: list[dict],
    is_multi_city: bool,
) -> None:
    """Append one row per sampled date-combo result.

    pair_results: [{"dates": list[str], "stops": int, "offer": FlightOffer}, ...]
    -- "dates" has 2 elements (dep, ret) for round-trip, 3 (dep, mid, ret)
    for multi-city. One row per run per combo, so history accumulates over
    time the same way the existing cheapest-overall CSVs do.
    """
    if not pair_results:
        return
    fields = _PAIR_CSV_FIELDS_MULTI_CITY if is_multi_city else _PAIR_CSV_FIELDS_ROUND_TRIP
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists()
    ts = datetime.now().isoformat(timespec="seconds")
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            writer.writeheader()
        for pr in pair_results:
            offer = pr["offer"]
            row = {
                "ts": ts,
                "route": route_name,
                "stops": pr["stops"],
                "price": offer.price,
                "currency": offer.currency,
                "airline": offer.airline,
                "details": offer.details,
            }
            if is_multi_city:
                row["dep_date"], row["mid_date"], row["ret_date"] = pr["dates"]
            else:
                row["dep_date"], row["ret_date"] = pr["dates"]
            writer.writerow(row)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_notifier.py -v`
Expected: all pass, including the 3 new tests.

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest -q`
Expected: all green (or the one known pre-existing cache-flake, unrelated).

- [ ] **Step 6: Commit**

```bash
git add code/notifier.py tests/test_notifier.py
git commit -m "notifier.py: add append_pair_rows for per-date-pair history"
```

---

### Task 3: Wire per-date-pair persistence into main.py

**Files:**
- Modify: `main.py`

**Interfaces:**
- Consumes: `code.searcher.search_round_trip`/`search_multi_city` now
  returning `(offers_by_stops, pair_results)` (Task 1); `code.notifier.append_pair_rows`
  (Task 2).
- Produces: new module-level constants `MULTI_CITY_PAIR_CSV_PATH`,
  `ROUND_TRIP_PAIR_CSV_PATH`; a new `pair_csv_path_by_route` dict passed to
  `write_html` (Task 4 will accept this new parameter).

- [ ] **Step 1: Modify `main.py`**

Add two new path constants right after the existing CSV path constants:

```python
MULTI_CITY_CSV_PATH = BASE_DIR / "data" / "prices.csv"
ROUND_TRIP_CSV_PATH = BASE_DIR / "data" / "round_trip_prices.csv"
MULTI_CITY_PAIR_CSV_PATH = BASE_DIR / "data" / "prices_by_pair.csv"
ROUND_TRIP_PAIR_CSV_PATH = BASE_DIR / "data" / "round_trip_prices_by_pair.csv"
```

Add a helper mirroring `history_path_for`, right after it:

```python
def pair_path_for(route: dict) -> Path:
    return ROUND_TRIP_PAIR_CSV_PATH if route["type"] == "round_trip" else MULTI_CITY_PAIR_CSV_PATH
```

Import the new writer:

```python
from code.notifier import FlightResult, append_to_csv, append_pair_rows, write_summary, send_route_notification
```

In `main()`, add a `pair_csv_path_by_route` dict alongside the existing
`csv_path_by_route` (same line, same pattern):

```python
    csv_path_by_route: dict = {r["name"]: history_path_for(r) for r in routes}
    pair_csv_path_by_route: dict = {r["name"]: pair_path_for(r) for r in routes}
```

Change the search call and result-unpacking (find `offers_by_stops = search_fn(route, route_search_cfg)`):

```python
        try:
            offers_by_stops, pair_results = search_fn(route, route_search_cfg)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue
```

Right after the existing per-stop-count loop (after
`if route_pairs and any(a.should_alert for _, a in route_pairs): send_route_notification(...)`,
still inside the `for route in routes:` loop), add:

```python
        if pair_results:
            is_multi_city = route["type"] != "round_trip"
            append_pair_rows(pair_path_for(route), csv_name, pair_results, is_multi_city)
```

Update the `write_html` call to pass the new dict:

```python
    write_html(routes, results_by_route, alerts_by_route, csv_path_by_route,
               csv_name_by_route, html_path, pair_csv_path_by_route)
```

- [ ] **Step 2: No new automated test for this task** — `main.py` has no
  existing unit tests (it's an integration entry point), and this is a
  small wiring change over already-tested pieces (Task 1's search functions,
  Task 2's writer). Verified by Task 5's live run instead.

- [ ] **Step 3: Run the full test suite**

Run: `cd ~/projects/claude/flight-bot && source .venv/bin/activate && python -m pytest -q`
Expected: this will FAIL right now — `write_html` doesn't accept a 7th
argument yet. That's expected; Task 4 updates `write_html`'s signature. Do
not treat this as a problem to fix in this task — just confirm the failure
is specifically a `TypeError` about `write_html`'s argument count, nothing
else broken.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "main.py: wire per-date-pair CSV persistence (write_html signature updated in next commit)"
```

---

### Task 4: Dashboard — fix airport-code extraction, replace blank columns, add per-pair table

**Files:**
- Modify: `code/html_writer.py`
- Test: `tests/test_html_writer.py` (new file — flight-bot has none yet)

**Interfaces:**
- Consumes: the two new `*_by_pair.csv` files (Task 2/3's output shape).
- Produces: `write_html(route_configs, results_by_route, alerts_by_route, csv_path_by_route, csv_name_by_route, html_path, pair_csv_path_by_route)` —
  new 7th parameter. `_load_pair_history(csv_path, route, is_multi_city) -> list[dict]`,
  `_latest_per_pair(rows, stops) -> list[dict]`, `_pair_table(rows, route_cfg, stops, is_multi_city) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_html_writer.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_html_writer.py -v`
Expected: FAIL — `_latest_per_pair` doesn't exist yet; the multi-city/round-trip
new-format `_airport_codes_from_details` tests fail (current implementation
returns the wrong `dest` for the new format — this is the bug being fixed).

- [ ] **Step 3: Modify `code/html_writer.py`**

Fix `_airport_codes_from_details` — replace its current body:

```python
def _airport_codes_from_details(details: str, route_cfg: dict) -> tuple[str, str]:
    """Best-effort (origin, outbound-destination) airport codes for a history row.

    Primary source is the itinerary text in the CSV `details` column (ground
    truth for the row). Both the legacy SerpAPI/Ignav format ("Outbound: ..."
    / "Inbound: ...") and the newer scrape format ("Leg 1: X→Y (date) — ..."
    / "Leg 2: ..." / "Leg 3: ...") delimit *leg 1* differently, but both need
    slicing to leg-1-only text before extracting codes: a multi-leg scrape
    trip's Leg 2/3 airports are a different direction entirely (e.g. the
    trip's final return-home airport), not a continuation of leg 1, and must
    not leak into "the outbound destination". Falls back to the route
    config's origin when `details` is absent (e.g. no data yet for this row).
    """
    if details:
        leg1_only = re.split(r"\nLeg 2:|\nInbound:", details)[0]
        codes = re.findall(r"\b[A-Z]{3}\b", leg1_only)
        if len(codes) >= 2:
            return codes[0], codes[-1]
    origin = route_cfg.get("origin", "")
    if not origin and route_cfg.get("segments"):
        origin = route_cfg["segments"][0].get("origin", "")
    return origin, ""
```

Delete `_parse_legs` and `_leg_summary` entirely (search for `def _parse_legs`
and `def _leg_summary` — both are now dead code, only ever called from
`_history_table`, which is being rewritten in the next step to not use them).

Replace `_history_table`'s body (same function signature, same "return `''`
when empty" contract) — remove the Outbound/Inbound/Total columns, add one
Details column:

```python
def _history_table(rows: list[dict], route_cfg: dict, stops: int) -> str:
    """Render one history section (heading + table) for a single stop count.

    Returns "" when there is no history for this stop count, so the caller can
    omit the section entirely. The `stops` filter is on the CSV `stops` column.
    """
    sub = [r for r in rows if r["stops"] == stops]
    if not sub:
        return ""

    label = "Nonstop" if stops == 0 else f"{stops} Stop" + ("s" if stops > 1 else "")
    lo = min(r["price"] for r in sub)

    html = [
        f"<h3>{label} — Price History</h3>",
        '<div class="hist-wrap"><table><tr>'
        "<th>Date</th><th>Price</th><th>Airline</th><th>Travel dates</th>"
        "<th>Details</th><th></th></tr>",
    ]
    for r in sub[:30]:  # cap at 30 rows
        cls = 'class="price-cell low"' if r["price"] <= lo else 'class="price-cell"'
        origin, dest = _airport_codes_from_details(r.get("details", ""), route_cfg)
        link_url = _gflights_link(origin, dest, r["departure"], r["return"])
        link = (
            f'<a href="{link_url}" target="_blank" rel="noopener">Search ↗</a>'
            if link_url else ""
        )
        details_html = "<br>".join(r["details"].splitlines()) if r.get("details") else "—"
        html.append(
            "<tr>"
            f"<td>{r['date']}</td>"
            f"<td {cls}>${r['price']:,.0f}</td>"
            f"<td>{r['airline']}</td>"
            f"<td class=\"dates-cell\">{_fmt_travel_dates(r['departure'], r['return'])}</td>"
            f"<td class=\"leg-cell\">{details_html}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )
    html.append("</table></div>")
    return "\n".join(html)
```

Add three new functions right after `_history_table` (before
`_render_segment_timeline`):

```python
def _load_pair_history(csv_path: Path, route: str, is_multi_city: bool, days: int = 90) -> list[dict]:
    """Return per-date-pair rows for this route within `days` days."""
    if not csv_path.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days)
    rows = []
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("route") != route:
                continue
            try:
                ts_dt = datetime.fromisoformat(row["ts"])
                if ts_dt < cutoff:
                    continue
                rows.append({
                    "ts": row["ts"],
                    "dep_date": row.get("dep_date", ""),
                    "mid_date": row.get("mid_date", "") if is_multi_city else "",
                    "ret_date": row.get("ret_date", ""),
                    "price": float(row["price"]),
                    "airline": row.get("airline", ""),
                    "details": row.get("details", ""),
                    "stops": int(row.get("stops", -1)),
                })
            except (ValueError, KeyError):
                continue
    return rows


def _latest_per_pair(rows: list[dict], stops: int) -> list[dict]:
    """Latest observation per unique date-combo, for one stop count, sorted
    cheapest-first — mirrors the "latest, not all-time" rule the top summary
    panel already uses, so a stale cheap sighting can't look bookable today."""
    sub = [r for r in rows if r["stops"] == stops]
    latest: dict = {}
    for r in sub:
        key = (r["dep_date"], r["mid_date"], r["ret_date"])
        if key not in latest or r["ts"] > latest[key]["ts"]:
            latest[key] = r
    return sorted(latest.values(), key=lambda r: r["price"])


def _pair_table(rows: list[dict], route_cfg: dict, stops: int, is_multi_city: bool) -> str:
    """One row per sampled date combination — answers "what does *this*
    specific trip cost", not just "what's the single cheapest one found
    today". Returns "" when empty, same convention as `_history_table`."""
    current = _latest_per_pair(rows, stops)
    if not current:
        return ""

    label = "Nonstop" if stops == 0 else f"{stops} Stop" + ("s" if stops > 1 else "")
    html = [
        f"<h3>{label} — All Sampled Dates</h3>",
        '<div class="hist-wrap"><table><tr><th>Outbound</th>'
        + ("<th>Mid</th>" if is_multi_city else "")
        + "<th>Return</th><th>Latest price</th><th>Details</th><th>As of</th><th></th></tr>",
    ]
    for r in current[:60]:
        origin, dest = _airport_codes_from_details(r.get("details", ""), route_cfg)
        # Leg 1 of a multi-city trip is one-way (BOS->NRT), not a round trip --
        # there's no "return date" for it, so leave ret blank there (_gflights_link
        # already handles a falsy ret by omitting "returning ..." from the query).
        # For round-trip, leg 1 and leg 2 really are outbound/return of one trip.
        ret_for_link = "" if is_multi_city else r["ret_date"]
        link_url = _gflights_link(origin, dest, r["dep_date"], ret_for_link)
        link = (
            f'<a href="{link_url}" target="_blank" rel="noopener">Search ↗</a>'
            if link_url else ""
        )
        details_html = "<br>".join(r["details"].splitlines()) if r.get("details") else "—"
        html.append(
            "<tr>"
            f"<td>{r['dep_date']}</td>"
            + (f"<td>{r['mid_date']}</td>" if is_multi_city else "")
            + f"<td>{r['ret_date']}</td>"
            f"<td class=\"price-cell\">${r['price']:,.0f}</td>"
            f"<td class=\"leg-cell\">{details_html}</td>"
            f"<td class=\"dates-cell\">{r['ts'][:10]}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )
    html.append("</table></div>")
    return "\n".join(html)
```

Update `_render_card` to accept and render the new pair tables — change its
signature and body:

```python
def _render_card(
    route_name: str,
    results: dict,        # {stop_count: FlightResult}
    alerts: dict,         # {stop_count: AlertResult}
    history: list[dict],
    route_cfg: dict,
    min_stops: int = 0,
    max_stops: int = 1,
    pair_rows: list[dict] | None = None,
    is_multi_city: bool = False,
) -> str:
    panel_0 = _render_stop_panel(0, results.get(0), alerts.get(0), history) if min_stops <= 0 <= max_stops else ""
    panel_1 = _render_stop_panel(1, results.get(1), alerts.get(1), history) if min_stops <= 1 <= max_stops else ""

    # Skip card entirely if no live data and no history
    if not results and not history:
        return ""

    hist_html = _history_table(history, route_cfg, 0) + _history_table(history, route_cfg, 1)
    if not hist_html:
        hist_html = "<h3>Price History</h3><p style='color:#aaa;font-size:0.85rem'>No history yet.</p>"

    pair_rows = pair_rows or []
    pair_html = (
        _pair_table(pair_rows, route_cfg, 0, is_multi_city)
        + _pair_table(pair_rows, route_cfg, 1, is_multi_city)
    )

    return f"""<div class="card">
  <div class="card-header">
    <div class="route-name">{route_name}</div>
  </div>
  <div class="card-body">
    <div class="panels">
      {panel_0}
      {panel_1}
    </div>
    {hist_html}
    {pair_html}
  </div>
</div>"""
```

Update `write_html`'s signature and body to load and pass through the pair
data:

```python
def write_html(
    route_configs: list[dict],
    results_by_route: dict,   # route_name → {stop_count: FlightResult}
    alerts_by_route: dict,    # route_name → {stop_count: AlertResult}
    csv_path_by_route: dict,
    csv_name_by_route: dict,
    html_path: Path,
    pair_csv_path_by_route: dict,
) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cards = []
    for route_cfg in route_configs:
        name = route_cfg["name"]
        csv_name = csv_name_by_route.get(name, name)
        route_results = results_by_route.get(name, {})
        route_alerts = alerts_by_route.get(name, {})
        csv_path = csv_path_by_route.get(name)
        history = _load_history(csv_path, csv_name) if csv_path else []
        is_multi_city = "segments" in route_cfg
        pair_csv_path = pair_csv_path_by_route.get(name)
        pair_rows = _load_pair_history(pair_csv_path, csv_name, is_multi_city) if pair_csv_path else []
        min_stops = int(route_cfg.get("min_stops", 0))
        max_stops = int(route_cfg.get("max_stops", 1))
        card = _render_card(name, route_results, route_alerts, history, route_cfg,
                            min_stops=min_stops, max_stops=max_stops,
                            pair_rows=pair_rows, is_multi_city=is_multi_city)
        if card:
            cards.append(card)

    body = "\n".join(cards) if cards else "<p style='color:#888'>No data yet.</p>"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flight Prices</title>
<style>{_CSS}</style>
</head>
<body>
<h1>Flight Prices</h1>
<p class="meta">Updated {now}</p>
{body}
<p class="disclaimer">Tracked prices come from Google Flights (scraped directly, or via a fare
API for routes explicitly configured to use one). The <b>Search&nbsp;↗</b> link opens an <i>independent</i> Google Flights
search for the same route and dates — it is a different seller, so its price can differ (in
either direction) from the tracked fare above. Treat it as a live sanity-check, not the exact
fare, and confirm the price on the airline's own site before booking.</p>
</body>
</html>"""

    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_html_writer.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest -q`
Expected: all green (or the one known pre-existing cache-flake, unrelated).
This should now also fix Task 3's expected `write_html` `TypeError` from
its Step 3 — confirm that failure is gone too.

- [ ] **Step 6: Commit**

```bash
git add code/html_writer.py tests/test_html_writer.py
git commit -m "html_writer.py: fix leg-1 airport-code extraction, replace blank Outbound/Inbound/Total with Details, add per-date-pair table"
```

---

### Task 5: Live verification

**Files:** none (verification only)

- [ ] **Step 1: Direct search-function smoke test — confirm pair_results is real and complete**

```bash
cd ~/projects/claude/flight-bot && source .venv/bin/activate && python3 -c "
import yaml
from code.searcher import search_multi_city
cfg = yaml.safe_load(open('config/routes.yaml'))
route = next(r for r in cfg['routes'] if r['name'] == 'Asia Grand Tour')
search_cfg = dict(cfg['search']); search_cfg.update(route.get('search', {})); search_cfg['provider'] = 'scrape'
search_cfg['sample_dates'] = 2
best, pairs = search_multi_city(route, search_cfg)
print('best:', {k: (v.price if v else None) for k, v in best.items()})
print('n pair results:', len(pairs))
for p in pairs[:3]:
    print(' ', p['dates'], p['stops'], p['offer'].price)
"
```
Expected: `n pair results` > 2 (multiple date/stay combos, not just the 2 winners) — confirms every sampled combo is actually being captured, not just the cheapest.

- [ ] **Step 2: STOP before running the full pipeline**

`main.py`'s `_deploy()` pushes to the live public `flight-bot-site` gh-pages
repo whenever `output/listings.html` changes. Do not run `python main.py` as
part of automated verification — ask the user for explicit confirmation
first, exactly as was done for the scraping-rewrite plan's equivalent step.

- [ ] **Step 3: (Only after explicit user confirmation) full pipeline run**

```bash
cd ~/projects/claude/flight-bot && source .venv/bin/activate && python main.py
```
Expected: both routes searched, `data/prices_by_pair.csv`/`data/round_trip_prices_by_pair.csv`
created with multiple rows (not just 2), `output/listings.html`/`site/index.html`
rendered with both the existing summary panels AND a new "All Sampled Dates"
table per stop-count showing several rows, each with full leg detail instead
of blank Outbound/Inbound/Total. Confirm the new CSVs and the rendered HTML
directly before considering this done — do not just trust exit code 0.

- [ ] **Step 4: Full test suite one more time**

```bash
python -m pytest -q
```
Expected: all green (or the one known pre-existing cache-flake, unrelated).

## Self-Review

**Spec coverage:**
- Persist every sampled combo, not just the winner ✓ (Task 1)
- New per-date-pair CSVs, 2-date and 3-date shapes ✓ (Task 2)
- Existing alert/summary system untouched ✓ (Task 1's fallback-provider
  one-line change and Task 3's wiring both leave `offers_by_stops`/
  `FlightResult`/`analyze`/`append_to_csv` completely alone)
- New "All Sampled Dates" table, kept alongside existing summary+history ✓
  (Task 4)
- Blank Outbound/Inbound/Total → real leg detail, in both the existing
  history table and the new table ✓ (Task 4)
- Fixed airport-code extraction for the new multi-leg format (a real,
  demonstrated bug — `codes[-1]` grabbing the wrong leg's airport for a
  3+-leg trip) ✓ (Task 4, with regression tests distinguishing the legacy
  connecting-flight case from the new multi-leg case)

**Placeholder scan:** none — every step has complete code, exact commands,
exact expected output.

**Type consistency:** `pair_results`/`all_results` shape
(`{"dates": list[str], "stops": int, "offer": FlightOffer}`) is identical
across Task 1 (producer), Task 2 (consumer via `append_pair_rows`), and
Task 3 (main.py's wiring) — checked field names and types match at each
handoff. `is_multi_city` boolean is threaded consistently from `main.py`
(`route["type"] != "round_trip"`) through to `html_writer.py`
(`"segments" in route_cfg` — an equivalent, independently-derived check,
intentionally not shared code across the two modules since `main.py` and
`html_writer.py` don't otherwise share config-interpretation helpers).
