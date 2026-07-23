# flight-bot Scraping Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace SerpAPI/Ignav as flight-bot's default price source with jal-bot's proven, free Google-Flights-scraping technique, for both routes ("Asia Grand Tour" multi-city and "BOS ↔ Beijing" round-trip), while keeping SerpAPI/Ignav available as an explicit per-route fallback.

**Architecture:** Vendor jal-bot's three scraping files (`browser.py`, `gflights.py`, `gflights_searcher.py`) into flight-bot, generalizing `gflights_searcher.py`'s public API from jal-bot's fixed 2-leg open-jaw shape to an arbitrary ordered list of legs (needed for the 3-leg Asia Grand Tour route; a round trip is just a 2-leg case of the same thing). Add `search_round_trip_scrape`/`search_multi_city_scrape` to `code/searcher.py`, dispatched via the existing `_provider(config)` mechanism, returning the same `{0: FlightOffer|None, 1: FlightOffer|None}` shape the existing SerpAPI/Ignav functions already return — so `main.py`, `notifier.py`, `analyzer.py`, and `html_writer.py` need **zero** changes.

**Tech Stack:** Python, Playwright (sync API), a system-installed `google-chrome` connected over CDP, PyYAML, pytest.

## Global Constraints

- Every task must leave `pytest` green (except the one pre-existing, unrelated failure noted below) before moving to the next task.
- SerpAPI/Ignav code, tests, and `.env.example` keys are **not removed** — `scrape` becomes the new default, the others remain explicit fallbacks.
- `main.py`, `code/notifier.py`, `code/analyzer.py`, `code/html_writer.py` are **not modified** by this plan — the whole point of matching the existing `{0:.., 1:..}` `FlightOffer` return shape is that these files don't need to change.
- One pre-existing, unrelated test failure exists on this branch: `tests/test_searcher.py::test_search_multi_city_returns_cheapest` fails because a stale on-disk API-response cache (`data/api_cache/`, 6-hour TTL) intercepts the test's mocked HTTP call. This is not caused by this plan and is not this plan's job to fix — confirm it's still the *only* failure at each checkpoint, don't try to fix it.
- Route/date-window decisions already made (do not re-litigate): Asia Grand Tour segments become BOS→NRT (stay 7–10d) → **KIX→SHA** (stay 14–18d, self-arranged SHA→HKG) → HKG→BOS; both routes' outbound window is 2026-10-06 → 2026-12-01 (Beijing's `date_end` must be computed as last-outbound-date + max-total-stay, not copied literally — see jal-bot's own 2026-07-23 date-window change and Task 5 below for the exact math).

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `code/gflights.py` | New (vendored, unchanged) | `gflights_url(legs, seat, adults)` — deep-link protobuf builder. Already generic over leg count; no changes needed. |
| `code/gflights_searcher.py` | New (vendored, **adapted**) | `search_all_options(page, legs, seat, adults)`, `parse_all_options`, `pick_cheapest_nonstop`, `pick_cheapest_alternative` — aria-label scraper/parser. Public entry point generalized from jal-bot's fixed 2-leg `(out_date, ret_date, iata)` signature to an arbitrary `legs: list[(dep, arr, date)]`, so it covers both the round-trip and 3-leg cases. Parsing logic itself is unchanged. |
| `code/browser.py` | New (vendored, port/profile/display changed) | `launch_browser()` — headful Chrome over CDP. New `CDP_PORT=9224`, `CHROME_PROFILE=chrome-flightbot`, `_XVFB_DISPLAY=":51"` — distinct from jal-bot/jal-shanghai-bot's shared `9223`/`chrome-jalbot`/`:50`, since flight-bot's own daily timer runs independently and could overlap with jal-bot's. |
| `code/searcher.py` | Modified | Add `_scrape_offers_by_stops`, `_scrape_build_offer`, `search_round_trip_scrape`, `search_multi_city_scrape`; update `_provider()`'s default and the `search_round_trip`/`search_multi_city` dispatchers to route to `scrape` by default. |
| `config/routes.yaml` | Modified | Both routes: `provider: scrape`. Asia Grand Tour: segment 2 becomes `KIX→SHA`. Beijing: date window matches Asia Grand Tour's. |
| `data/archive/` | New | Old `prices.csv`/`round_trip_prices.csv` moved here (schema + date window both changing at once). |
| `requirements.txt` | Modified | Add `playwright>=1.47`. |
| `flight-bot.timer` | Modified | Every-6-days → daily. |
| `CLAUDE.md`, `README.md` | Modified | Document new default provider, route shape, archived data, timer cadence. |
| `tests/test_gflights.py` | New | `gflights_url` sanity tests (2-leg and 3-leg). |
| `tests/test_gflights_searcher.py` | New | Parser tests, ported from jal-bot (unchanged logic) + one new test for the generalized `legs`-based signature. |
| `tests/test_searcher.py` | Modified | New tests for `search_round_trip_scrape`/`search_multi_city_scrape` (mocked). |

---

### Task 1: Vendor the Google Flights deep-link builder

**Files:**
- Create: `code/gflights.py`
- Test: `tests/test_gflights.py`

**Interfaces:**
- Produces: `gflights_url(legs: list[tuple[str,str,str]], seat: int = 1, adults: int = 1) -> str` and `SEAT: dict[str,int]` — used by Task 2's scraper and Task 4's searcher functions.

- [ ] **Step 1: Write the failing test**

Create `tests/test_gflights.py`:

```python
import base64
from code.gflights import gflights_url, SEAT


def _decode_body(url: str) -> bytes:
    tfs = url.split("tfs=")[1].split("&")[0]
    # gflights_url quotes the base64 string; undo that for decoding
    from urllib.parse import unquote
    return base64.b64decode(unquote(tfs))


def test_seat_enum_values():
    assert SEAT["economy"] == 1
    assert SEAT["business"] == 3


def test_two_leg_url_contains_both_airport_pairs():
    url = gflights_url([("BOS", "PEK", "2026-10-06"), ("PEK", "BOS", "2026-10-27")])
    assert url.startswith("https://www.google.com/travel/flights?tfs=")
    body = _decode_body(url)
    for code in (b"BOS", b"PEK", b"2026-10-06", b"2026-10-27"):
        assert code in body


def test_three_leg_url_contains_all_three_airport_pairs():
    url = gflights_url([
        ("BOS", "NRT", "2026-10-13"),
        ("KIX", "SHA", "2026-10-21"),
        ("HKG", "BOS", "2026-11-06"),
    ])
    body = _decode_body(url)
    for code in (b"BOS", b"NRT", b"KIX", b"SHA", b"HKG"):
        assert code in body


def test_adults_and_seat_affect_url_length():
    one_adult = gflights_url([("BOS", "PEK", "2026-10-06")], seat=1, adults=1)
    two_adults = gflights_url([("BOS", "PEK", "2026-10-06")], seat=1, adults=2)
    assert len(two_adults) > len(one_adult)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/projects/claude/flight-bot && source .venv/bin/activate && python -m pytest tests/test_gflights.py -v`
Expected: FAIL / ERROR — `ModuleNotFoundError: No module named 'code.gflights'`

- [ ] **Step 3: Create `code/gflights.py`** (vendored verbatim from `~/projects/claude/jal-bot/code/gflights.py` — no changes needed, it already takes a generic leg list)

```python
"""Google Flights multi-city deep-link builder, shared by the searcher (which
opens these URLs to read live prices) and the dashboard (which links out to
the same search a viewer can re-run).

Wire format of Google's flight-search protobuf (field numbers reverse-engineered,
same schema the `fast-flights` library uses):
  Airport   { string code = 2 }
  FlightData{ string date = 2; repeated Airport from = 13; repeated Airport to = 14 }
  TFSData   { repeated FlightData legs = 3; repeated Passenger pax = 8;
              Seat seat = 9; TripType trip = 19 }   (trip: multi-city = 3)

Vendored from jal-bot's code/gflights.py (2026-07-23) — see
docs/superpowers/specs/2026-07-23-scraping-rewrite-design.md. A round-trip is
just a 2-leg multi-city search (A->B, then B->A), so this same builder covers
both of flight-bot's route types; no changes were needed from the original.
"""
import base64
from urllib.parse import quote

# Google Flights cabin enum (Seat): economy=1, premium-economy=2, business=3, first=4
SEAT = {"economy": 1, "premium-economy": 2, "premium economy": 2, "business": 3, "first": 4}


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def _ld(field: int, payload: bytes) -> bytes:      # length-delimited (wire type 2)
    return _varint(field << 3 | 2) + _varint(len(payload)) + payload


def _vf(field: int, value: int) -> bytes:          # varint field (wire type 0)
    return _varint(field << 3) + _varint(value)


def _leg(dep: str, arr: str, date: str) -> bytes:
    return (_ld(2, date.encode())
            + _ld(13, _ld(2, dep.encode()))
            + _ld(14, _ld(2, arr.encode())))


def gflights_url(legs, seat: int = 1, adults: int = 1) -> str:
    """legs: list of (dep_iata, arr_iata, 'YYYY-MM-DD'). Returns a Google Flights
    multi-city search URL with every leg, cabin, and passenger count pre-filled."""
    body = b"".join(_ld(3, _leg(*leg)) for leg in legs)
    body += b"".join(_vf(8, 1) for _ in range(adults))   # passengers: adult=1
    body += _vf(9, seat) + _vf(19, 3)                    # seat, trip=multi-city
    tfs = base64.b64encode(body).decode()
    return f"https://www.google.com/travel/flights?tfs={quote(tfs)}&curr=USD&hl=en"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_gflights.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add code/gflights.py tests/test_gflights.py
git commit -m "Vendor jal-bot's Google Flights deep-link builder (unchanged)"
```

---

### Task 2: Vendor and generalize the aria-label scraper

**Files:**
- Create: `code/gflights_searcher.py`
- Test: `tests/test_gflights_searcher.py`

**Interfaces:**
- Consumes: `code.gflights.gflights_url(legs, seat, adults)` (Task 1)
- Produces: `search_all_options(page, legs, seat=1, adults=1) -> list[dict]`, `parse_all_options(aria_labels) -> list[dict]`, `pick_cheapest_nonstop(options) -> dict|None`, `pick_cheapest_alternative(options) -> dict|None` — each option dict has keys `price, currency, airline, stops, dep_airport, dep_time, dep_date, arr_airport, arr_time, arr_date, duration_min, layover_min, layover_airport`. Used by Task 4's `search_*_scrape` functions.

- [ ] **Step 1: Write the failing test**

Create `tests/test_gflights_searcher.py` (parser fixtures ported verbatim from jal-bot's `tests/test_gflights_searcher.py` — same real captured aria-labels, since `parse_all_options`/`pick_cheapest_nonstop`/`pick_cheapest_alternative` are unchanged logic; one new test added at the bottom for the generalized `legs`-based signature):

```python
"""Unit tests for the aria-label parser — no browser needed.

Fixture labels are the real strings jal-bot captured from a live Google
Flights page (see jal-bot's docs/superpowers/specs/ for the feasibility
spike). Only the summary card's opening sentence ("From $X total. <stops>
flight with <airline>.") is load-bearing for parsing; trailing itinerary
detail is real but irrelevant. Ported into flight-bot 2026-07-23 — see
docs/superpowers/specs/2026-07-23-scraping-rewrite-design.md.
"""
from code.gflights_searcher import (
    parse_all_options,
    parse_nonstop_cheapest,
    pick_cheapest_alternative,
    pick_cheapest_nonstop,
    search_all_options,
)

AIR_CANADA_1STOP = (
    "From 616 US dollars total. 1 stop flight with Air Canada. Leaves Boston "
    "Logan International Airport at 10:40 AM on Tuesday, September 15 and "
    "arrives at Narita International Airport at 3:25 PM on Wednesday, "
    "September 16. Total duration 15 hr 45 min.  Layover (1 of 1) is a 50 min "
    "layover at Montréal-Pierre Elliott Trudeau International Airport in "
    "Montreal. Select flight"
)
AMERICAN_1STOP = (
    "From 1008 US dollars total. 1 stop flight with American. Leaves Boston "
    "Logan International Airport at 7:45 AM on Tuesday, September 15 and "
    "arrives at Narita International Airport at 3:00 PM on Wednesday, "
    "September 16. Total duration 18 hr 15 min.  Layover (1 of 1) is a 43 min "
    "layover at Dallas Fort Worth International Airport in Dallas. Select flight"
)
UNITED_1STOP = (
    "From 1021 US dollars total. 1 stop flight with United. Leaves Boston "
    "Logan International Airport at 7:30 AM on Tuesday, September 15 and "
    "arrives at Haneda Airport at 1:35 PM on Wednesday, September 16. Total "
    "duration 17 hr 5 min.  Layover (1 of 1) is a 1 hr 6 min layover at Newark "
    "Liberty International Airport in Newark. Select flight"
)
JAL_NONSTOP = (
    "From 1085 US dollars total. Nonstop flight with JAL. Leaves Boston Logan "
    "International Airport at 1:00 PM on Tuesday, September 15 and arrives at "
    "Narita International Airport at 4:00 PM on Wednesday, September 16. Total "
    "duration 14 hr. Select flight"
)
UNPARSEABLE_NOISE = "616 US dollars"  # a bare price fragment, not a summary card


def test_picks_the_only_nonstop_card():
    fare = parse_nonstop_cheapest([AIR_CANADA_1STOP, AMERICAN_1STOP, JAL_NONSTOP])
    assert fare == {"price": 1085.0, "currency": "USD", "airline": "JAL"}


def test_ignores_connecting_itineraries_even_when_cheaper():
    fare = parse_nonstop_cheapest([AIR_CANADA_1STOP, JAL_NONSTOP])
    assert fare["price"] == 1085.0


def test_returns_none_when_no_nonstop_option():
    fare = parse_nonstop_cheapest([AIR_CANADA_1STOP, AMERICAN_1STOP, UNITED_1STOP])
    assert fare is None


def test_returns_none_for_empty_or_noisy_input():
    assert parse_nonstop_cheapest([]) is None
    assert parse_nonstop_cheapest([None, UNPARSEABLE_NOISE, ""]) is None


def test_takes_cheapest_among_multiple_nonstop_cards():
    jal_pricier = JAL_NONSTOP.replace("1085", "1220")
    fare = parse_nonstop_cheapest([jal_pricier, JAL_NONSTOP])
    assert fare["price"] == 1085.0


def test_parse_all_options_extracts_layover_detail():
    options = parse_all_options([AIR_CANADA_1STOP])
    assert len(options) == 1
    o = options[0]
    assert o["price"] == 616.0
    assert o["airline"] == "Air Canada"
    assert o["stops"] == "1 stop"
    assert o["dep_time"] == "10:40 AM"
    assert o["arr_time"] == "3:25 PM"
    assert o["duration_min"] == 15 * 60 + 45
    assert o["layover_min"] == 50
    assert "Montréal" in o["layover_airport"]


def test_parse_all_options_nonstop_has_no_layover():
    options = parse_all_options([JAL_NONSTOP])
    o = options[0]
    assert o["stops"] == "Nonstop"
    assert o["duration_min"] == 14 * 60
    assert o["layover_min"] is None
    assert o["layover_airport"] is None


def test_parse_all_options_sorted_cheapest_first():
    options = parse_all_options([UNITED_1STOP, JAL_NONSTOP, AIR_CANADA_1STOP, AMERICAN_1STOP])
    assert [o["price"] for o in options] == [616.0, 1008.0, 1021.0, 1085.0]


def test_parse_all_options_skips_unparseable():
    options = parse_all_options([None, UNPARSEABLE_NOISE, "", AIR_CANADA_1STOP])
    assert len(options) == 1
    assert options[0]["airline"] == "Air Canada"


def test_pick_cheapest_alternative_finds_cheapest_connecting_option():
    options = parse_all_options([JAL_NONSTOP, AMERICAN_1STOP, AIR_CANADA_1STOP])
    alt = pick_cheapest_alternative(options)
    assert alt["airline"] == "Air Canada"
    assert alt["price"] == 616.0


def test_pick_cheapest_alternative_none_when_only_nonstop():
    options = parse_all_options([JAL_NONSTOP])
    assert pick_cheapest_alternative(options) is None


def test_pick_cheapest_nonstop_matches_module_level_helper():
    options = parse_all_options([AIR_CANADA_1STOP, JAL_NONSTOP])
    assert pick_cheapest_nonstop(options) == parse_nonstop_cheapest(
        [AIR_CANADA_1STOP, JAL_NONSTOP]
    )


def test_search_all_options_stamps_every_option_with_the_legs_it_was_given(monkeypatch):
    """search_all_options takes an arbitrary ordered legs list (not jal-bot's
    fixed 2-leg out_date/ret_date/iata signature) — this is the generalization
    this file makes over the jal-bot original, needed for the 3-leg Asia Grand
    Tour route. Mock the page-loading step; only the leg-stamping is tested here."""
    monkeypatch.setattr(
        "code.gflights_searcher._load_result_labels",
        lambda page, legs, seat, adults: [JAL_NONSTOP, AIR_CANADA_1STOP],
    )
    legs = [
        ("BOS", "NRT", "2026-10-13"),
        ("KIX", "SHA", "2026-10-21"),
        ("HKG", "BOS", "2026-11-06"),
    ]
    options = search_all_options(page=None, legs=legs, seat=1, adults=1)
    assert len(options) == 2
    for o in options:
        assert o["legs"] == [
            {"dep": "BOS", "arr": "NRT", "date": "2026-10-13"},
            {"dep": "KIX", "arr": "SHA", "date": "2026-10-21"},
            {"dep": "HKG", "arr": "BOS", "date": "2026-11-06"},
        ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gflights_searcher.py -v`
Expected: FAIL / ERROR — `ModuleNotFoundError: No module named 'code.gflights_searcher'`

- [ ] **Step 3: Create `code/gflights_searcher.py`** (vendored from jal-bot's `code/gflights_searcher.py`, with the public entry point generalized from `(page, out_date, ret_date, iata, seat, adults)` to `(page, legs, seat, adults)` — parsing logic (`parse_all_options`, `pick_cheapest_nonstop`, `pick_cheapest_alternative`) is byte-for-byte unchanged)

```python
"""Open-jaw / multi-city outbound fare options, read live off Google Flights.

Google Flights has no true multi-city price calendar, so each itinerary is one
page load: build the `tfs` deep-link (code/gflights.py), open it on the
anti-bot browser, and read every fare card's summary `aria-label` — each one is
"From $X total. <Nonstop|N stop> flight with <airline>. Leaves <airport> at
<time> on <date> and arrives at <airport> at <time> on <date>. Total duration
<Xhr Ymin>. [Layover ... at <airport> in <city>.]" — where the stop-count /
airline / times / duration / layover describe the FIRST leg of the search
only, and the price is the whole trip's total. `search_all_options` returns
every option parsed this way, cheapest first, at zero extra scraping cost (one
page load already lists them all). `pick_cheapest_nonstop` isolates the
cheapest nonstop-first-leg option; `pick_cheapest_alternative` surfaces the
cheapest connecting (non-nonstop) option for comparison.

Vendored from jal-bot's code/gflights_searcher.py (2026-07-23) — see
docs/superpowers/specs/2026-07-23-scraping-rewrite-design.md. jal-bot's public
entry point took a fixed 2-leg open-jaw shape (out_date, ret_date, iata dict);
generalized here to an arbitrary ordered `legs: list[(dep, arr, date)]`, since
flight-bot's Asia Grand Tour route is a 3-leg multi-city trip, not a 2-leg
open-jaw. Live-verified 2026-07-23 that the aria-label format and parsing hold
for a real 3-leg trip, not just the 2-leg case. Parsing logic itself
(`parse_all_options`/`pick_cheapest_nonstop`/`pick_cheapest_alternative`) is
unchanged.
"""
import re
from datetime import date

from code.gflights import gflights_url

# Full summary card: "From $X total. <Nonstop|N stop> flight with <airline>.
# Leaves <airport> at <time> on <weekday, month day> and arrives at <airport>
# at <time> on <weekday, month day>[ (+1 day)]. Total duration <Xhr Ymin>. "
# [ Layover (i of n) is a <Zmin> layover at <airport> in <city>[. ...more]]
_CARD_RE = re.compile(
    r"^From ([\d,]+) US dollars total\.\s*(Nonstop|\d+ stop) flight with ([^.]+)\.\s*"
    r"Leaves ([^.]+?) at ([\d:]+\s*[AP]M) on ([^.]+?) and arrives at ([^.]+?) "
    r"at ([\d:]+\s*[AP]M) on ([^.]+?)\.\s*Total duration ([^.]+)\."
)
_LAYOVER_RE = re.compile(
    r"Layover \(1 of 1\) is an? ([^.]+?) layover at ([^.]+?) in "
)


def _duration_to_min(text: str) -> int | None:
    """'15 hr 45 min' / '14 hr' / '45 min' -> minutes."""
    h = re.search(r"(\d+)\s*hr", text)
    m = re.search(r"(\d+)\s*min", text)
    if not h and not m:
        return None
    return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)


def parse_all_options(aria_labels: list) -> list:
    """Parse every fare-card summary aria-label into a structured option.

    Each option: {price, currency, airline, stops ("Nonstop" or "N stop"),
    dep_airport, dep_time, dep_date, arr_airport, arr_time, arr_date,
    duration_min, layover_min, layover_airport}. `stops`/`airline`/duration
    describe the FIRST leg of the search only; price is the whole trip's
    total. Sorted cheapest-first. Malformed/unrelated labels are skipped.
    """
    options = []
    for label in aria_labels:
        if not label:
            continue
        m = _CARD_RE.match(label)
        if not m:
            continue
        lay = _LAYOVER_RE.search(label)
        options.append({
            "price": float(m.group(1).replace(",", "")),
            "currency": "USD",
            "airline": m.group(3),
            "stops": m.group(2),
            "dep_airport": m.group(4),
            "dep_time": m.group(5),
            "dep_date": m.group(6),
            "arr_airport": m.group(7),
            "arr_time": m.group(8),
            "arr_date": m.group(9),
            "duration_min": _duration_to_min(m.group(10)),
            "layover_min": _duration_to_min(lay.group(1)) if lay else None,
            "layover_airport": lay.group(2) if lay else None,
        })
    return sorted(options, key=lambda o: o["price"])


def pick_cheapest_nonstop(options: list) -> dict | None:
    """From an already-parsed options list, return the cheapest NONSTOP-first-leg
    one as {"price", "currency", "airline"}, or None if none present."""
    nonstop = [o for o in options if o["stops"] == "Nonstop"]
    if not nonstop:
        return None
    cheapest = min(nonstop, key=lambda o: o["price"])
    return {"price": cheapest["price"], "currency": "USD", "airline": cheapest["airline"]}


def pick_cheapest_alternative(options: list) -> dict | None:
    """From an already-parsed options list, return the cheapest CONNECTING
    (non-nonstop) option, or None if every option is nonstop / there are none."""
    connecting = [o for o in options if o["stops"] != "Nonstop"]
    return min(connecting, key=lambda o: o["price"]) if connecting else None


def parse_nonstop_cheapest(aria_labels: list) -> dict | None:
    """From a page's fare-card aria-labels, return the cheapest NONSTOP-first-leg
    fare as {"price": float, "currency": "USD", "airline": str}, or None."""
    return pick_cheapest_nonstop(parse_all_options(aria_labels))


def _dismiss_consent(page):
    for text in ("Accept all", "Reject all", "I agree", "Accept"):
        try:
            btn = page.get_by_role("button", name=text)
            if btn.count():
                btn.first.click(timeout=3000)
                page.wait_for_timeout(1200)
                return
        except Exception:
            pass


def _load_result_labels(page, legs, seat: int, adults: int) -> list:
    """Open the Google Flights search for these legs and return every fare
    card's summary aria-label, unparsed. legs: list of
    (dep_iata, arr_iata, 'YYYY-MM-DD')."""
    url = gflights_url(legs, seat=seat, adults=adults)
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    _dismiss_consent(page)
    try:
        page.wait_for_selector('[aria-label*="dollar" i]', timeout=25_000)
    except Exception:
        page.wait_for_timeout(6_000)
    page.wait_for_timeout(2_000)  # let all result cards render, not just the first batch

    return page.eval_on_selector_all(
        '[aria-label*="dollar" i]',
        "els => els.map(e => e.getAttribute('aria-label'))",
    )


def search_all_options(page, legs, seat: int = 1, adults: int = 1) -> list:
    """Return every outbound option (nonstop and connecting) for this
    itinerary, cheapest first. legs: list of (dep_iata, arr_iata, 'YYYY-MM-DD'),
    2 legs for a round trip, 3+ for multi-city."""
    labels = _load_result_labels(page, legs, seat, adults)
    options = parse_all_options(labels)
    for o in options:
        o["legs"] = [{"dep": l[0], "arr": l[1], "date": l[2]} for l in legs]
    return options
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_gflights_searcher.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add code/gflights_searcher.py tests/test_gflights_searcher.py
git commit -m "Vendor jal-bot's aria-label scraper, generalized to arbitrary leg count"
```

---

### Task 3: Vendor the headful-Chrome browser launcher

**Files:**
- Create: `code/browser.py`

**Interfaces:**
- Produces: `launch_browser() -> (playwright, browser, page)` — used by Task 4's `search_*_scrape` functions.

- [ ] **Step 1: Create `code/browser.py`** (vendored from jal-bot's `code/browser.py`, with a distinct CDP port/profile/display so flight-bot's own daily timer can't collide with jal-bot's/jal-shanghai-bot's shared `9223`/`chrome-jalbot`/`:50`)

```python
"""Headful Chrome launcher for flight-bot.

Provides one public helper:

    launch_browser() -> (playwright, browser, page)
        Starts Xvfb (if needed), launches google-chrome with --use-angle=vulkan
        for real-GPU WebGL on a virtual display, connects Playwright over CDP,
        and returns a ready Page.

Vendored from jal-bot's code/browser.py (2026-07-23), itself adapted from
rental-car-bot/code/costco_searcher.py — see
docs/superpowers/specs/2026-07-23-scraping-rewrite-design.md. CDP_PORT,
CHROME_PROFILE, and _XVFB_DISPLAY are changed from jal-bot's values (9223,
chrome-jalbot, :50 — already shared with jal-shanghai-bot) to avoid any
collision if flight-bot's and jal-bot's independent daily timers ever overlap
in wall-clock time.
"""

import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Tuple

from playwright.sync_api import Page, sync_playwright

# ── CDP / profile config ──────────────────────────────────────────────────────

CDP_PORT = 9224                                         # distinct from jal-bot/jal-shanghai-bot's 9223
CDP_URL  = f"http://localhost:{CDP_PORT}"
CHROME_PROFILE = Path.home() / ".cache" / "chrome-flightbot"

# Virtual display geometry; Xvfb is allocated lazily (once per process).
_XVFB_DISPLAY = ":51"                                   # distinct from jal-bot's :50
_XVFB_PROC: subprocess.Popen | None = None


# ── Xvfb ─────────────────────────────────────────────────────────────────────

def _ensure_xvfb() -> str:
    """Start our own Xvfb virtual display if it isn't already running.

    Returns the DISPLAY string (e.g. ":51") to pass to Chrome.

    Deliberately ignores any inherited `DISPLAY` from the environment. Under
    the systemd --user timer, `DISPLAY=:1` (the real logged-in desktop) is
    imported into the user session's environment, so reusing it pops a
    visible Chrome window on the user's screen on every scheduled run.
    `--use-angle=vulkan` gives real-GPU WebGL on a virtual display regardless,
    so there's never a reason to touch the real one.
    """
    global _XVFB_PROC

    if _XVFB_PROC is not None and _XVFB_PROC.poll() is None:
        return _XVFB_DISPLAY

    xvfb = shutil.which("Xvfb")
    if xvfb is None:
        raise RuntimeError(
            "Xvfb not found.  Install xvfb (apt install xvfb) or set DISPLAY "
            "to an active X display before calling launch_browser()."
        )

    _XVFB_PROC = subprocess.Popen(
        [xvfb, _XVFB_DISPLAY, "-screen", "0", "1920x1080x24"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    return _XVFB_DISPLAY


# ── Chrome CDP management ─────────────────────────────────────────────────────

def _chrome_up() -> bool:
    """True if a Chrome DevTools endpoint is already listening on the CDP port."""
    try:
        urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2)
        return True
    except Exception:
        return False


def _ensure_chrome() -> None:
    """Make sure a real, headful google-chrome is listening on the CDP port.

    Reuses an already-running instance so cookies warm across runs.
    Otherwise launches one against CHROME_PROFILE with --use-angle=vulkan,
    which routes WebGL through the GPU via Vulkan directly from /dev/dri —
    bypassing the X server — so WebGL is real even on an Xvfb virtual display.
    """
    if _chrome_up():
        return

    display = _ensure_xvfb()

    chrome = shutil.which("google-chrome") or "/usr/bin/google-chrome"
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)

    # A stale singleton lock makes a second launch silently forward to the old
    # instance and DROP --remote-debugging-port, so clear it first.
    for lock in CHROME_PROFILE.glob("Singleton*"):
        try:
            lock.unlink()
        except OSError:
            pass

    env = dict(os.environ)
    env["DISPLAY"] = display

    subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={CHROME_PROFILE}",
            "--use-angle=vulkan",          # real-GPU WebGL on a virtual display
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,            # outlive this process for cookie reuse
        env=env,
    )

    for _ in range(40):
        if _chrome_up():
            return
        time.sleep(0.5)

    raise RuntimeError(
        f"Chrome did not come up on {CDP_URL}.  "
        f"Headful Chrome needs an X display (DISPLAY={display}); "
        f"check that Xvfb started and that google-chrome is installed."
    )


# ── Public API ────────────────────────────────────────────────────────────────

def launch_browser() -> Tuple[object, object, Page]:
    """Launch a headful Chrome on a virtual Xvfb display and return a Playwright Page.

    Returns:
        (playwright, browser, page) — a 3-tuple where *page* is ready for
        navigation. The caller is responsible for closing *browser* (and
        optionally stopping Playwright) when done.

    Chrome is connected over CDP and reused across calls within the same
    process (and across daily runs if chrome-flightbot is already running).
    """
    _ensure_chrome()
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(CDP_URL)
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()
    return pw, browser, page
```

- [ ] **Step 2: Verify Playwright is importable and google-chrome is present**

Run: `cd ~/projects/claude/flight-bot && source .venv/bin/activate && python -c "import playwright; import shutil; assert shutil.which('google-chrome'), 'google-chrome not found'; print('ok')"`
Expected: `ModuleNotFoundError: No module named 'playwright'` at this point — that's expected, Task 6 adds it to `requirements.txt` and installs it. If `google-chrome` is also missing, stop and tell the user — every other bot on this machine depends on it already, so its absence would be a different, bigger problem worth surfacing, not silently working around.

- [ ] **Step 3: Commit** (browser.py has no standalone unit test — it's exercised for real in Task 7's live verification, same as jal-bot's `browser.py` has no unit test)

```bash
git add code/browser.py
git commit -m "Vendor jal-bot's Akamai-defeating headful Chrome launcher, new port/profile/display"
```

---

### Task 4: Add scrape-provider search functions to `code/searcher.py`

**Files:**
- Modify: `code/searcher.py`
- Test: `tests/test_searcher.py`

**Interfaces:**
- Consumes: `code.browser.launch_browser()` (Task 3), `code.gflights_searcher.search_all_options(page, legs, seat, adults)` (Task 2), `code.gflights.SEAT` (Task 1)
- Produces: `search_round_trip_scrape(route, config) -> dict`, `search_multi_city_scrape(route, config) -> dict` — both return `{0: FlightOffer|None, 1: FlightOffer|None}`, same shape `search_round_trip`/`search_multi_city` already return for the other providers. `search_round_trip`/`search_multi_city` now default to `scrape` instead of `serpapi` when no provider matches `ignav`/`serpapi`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_searcher.py` (append at the end of the file; keep all existing imports and tests as-is):

```python
def test_provider_defaults_to_scrape_when_unset(monkeypatch):
    monkeypatch.delenv("FLIGHT_PROVIDER", raising=False)
    from code.searcher import _provider
    assert _provider({}) == "scrape"


def test_search_round_trip_scrape_returns_nonstop_and_onestop(monkeypatch):
    monkeypatch.setattr("code.searcher.launch_browser", lambda: (MagicMock(), MagicMock(), MagicMock()))

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
    result = search_round_trip(route, config)

    assert result[0].price == 944.0
    assert result[0].airline == "Hainan"
    assert result[0].departure_date == "2026-10-06"
    assert result[1].price == 780.0
    assert result[1].airline == "United"
    assert "Hainan" in result[0].details


def test_search_multi_city_scrape_returns_nonstop_and_onestop(monkeypatch):
    monkeypatch.setattr("code.searcher.launch_browser", lambda: (MagicMock(), MagicMock(), MagicMock()))

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
    result = search_multi_city(route, config)

    assert result[0].price == 2071.0
    assert result[0].airline == "JAL"
    assert result[1].price == 1259.0
    assert result[1].airline == "Air Canada"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_searcher.py -k scrape -v`
Expected: FAIL — `AttributeError`/`ImportError` (no `_provider` default of "scrape", no `scrape_search_all_options` in `code.searcher`, `search_round_trip`/`search_multi_city` don't dispatch to a scrape function yet)

- [ ] **Step 3: Modify `code/searcher.py`**

First, update the imports at the top of the file (add after the existing `from dotenv import load_dotenv` / `load_dotenv(...)` lines):

```python
from code.browser import launch_browser
from code.gflights import SEAT as _GFLIGHTS_SEAT
from code.gflights_searcher import search_all_options as scrape_search_all_options
```

Change the `_provider` function's default from `"serpapi"` to `"scrape"`:

```python
def _provider(config: dict) -> str:
    return str(config.get("provider") or os.environ.get("FLIGHT_PROVIDER", "scrape")).lower()
```

Add these new functions right before `search_round_trip_serpapi` (i.e., just after `_ignav_offers_by_stops`, before the `# ── Round-trip search` section comment):

```python
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
```

Finally, update the two dispatch functions at the bottom of the file:

```python
def search_round_trip(route: dict, config: dict, stops_filter: int = 2) -> dict:
    provider = _provider(config)
    if provider == "ignav":
        return search_round_trip_ignav(route, config)
    if provider == "serpapi":
        return search_round_trip_serpapi(route, config)
    return search_round_trip_scrape(route, config)


def search_multi_city(route: dict, config: dict, stops_filter: int = 2) -> dict:
    provider = _provider(config)
    if provider == "ignav":
        return search_multi_city_ignav(route, config)
    if provider == "serpapi":
        return search_multi_city_serpapi(route, config)
    return search_multi_city_scrape(route, config)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_searcher.py -v`
Expected: all pass except the one pre-existing `test_search_multi_city_returns_cheapest` failure (stale API cache, unrelated — confirm it's still the *only* failure)

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest -q`
Expected: same pre-existing single failure, everything else passes

- [ ] **Step 6: Commit**

```bash
git add code/searcher.py tests/test_searcher.py
git commit -m "Add scrape provider to searcher.py; scrape is now the default"
```

---

### Task 5: Update route config and archive old data

**Files:**
- Modify: `config/routes.yaml`
- Create: `data/archive/` (moved-into)

**Interfaces:**
- None (config-only; consumed by `main.py` at runtime, no code changes needed here).

- [ ] **Step 1: Compute Beijing's new `date_end`**

Same trap as jal-bot's own 2026-07-23 date-window change: `date_end` here is a return-by cutoff, not the last outbound date, and outbound sampling is truncated backward from it by `_max_total_stay()`. Beijing's `max_total_stay` = 21 days (its `stay_min`/`stay_max` are both 21). To make outbound sampling actually cover 2026-10-06 → 2026-12-01 (matching Asia Grand Tour), `date_end` must be `2026-12-01 + 21 days = 2026-12-22`.

Run this to confirm before editing the file:

```bash
cd ~/projects/claude/flight-bot && source .venv/bin/activate && python3 -c "
from datetime import date, timedelta
print(date(2026,12,1) + timedelta(days=21))
"
```
Expected output: `2026-12-22`

- [ ] **Step 2: Edit `config/routes.yaml`**

Replace the entire `routes:` section with:

```yaml
routes:
  - name: "Asia Grand Tour"
    type: multi_city
    provider: scrape
    date_start: "2026-10-06"   # matches jal-bot's outbound window
    date_end: "2026-12-29"     # Dec 1 (last outbound day) + 28-day max total stay
                                # (10-day BOS-NRT + 18-day KIX-SHA), so late-outbound
                                # departures' returns aren't cut off -- see
                                # _route_dates()/_max_total_stay() in code/searcher.py
    weekdays: [Tue, Thu]       # Tue/Thu departures tend to be cheapest
    sample_dates: 30           # high -> sampler returns EVERY Tue/Thu in the viable window
    segments:
      - origin: BOS
        destination: NRT
        stay_min: 7
        stay_max: 10
      - origin: KIX
        destination: SHA
        stay_min: 14
        stay_max: 18            # covers Shanghai time + self-arranged SHA->HKG leg + HK time
      - origin: HKG
        destination: BOS
    max_stops: 0   # each leg nonstop: BOS→NRT, KIX→SHA, HKG→BOS
    # Middle segment changed from KIX->HKG to KIX->SHA (Shanghai) on 2026-07-23,
    # with SHA->HKG self-arranged/untracked (same convention as the untracked
    # NRT->KIX gap) -- see
    # docs/superpowers/specs/2026-07-23-scraping-rewrite-design.md.
    # provider changed serpapi->scrape same day: see that same spec for why.

  - name: "BOS ↔ Beijing (PEK)"
    csv_name: "Boston-China 3-week"   # matches historical CSV data (unchanged so
                                       # price history from the wider-destination
                                       # era stays attached to this same route)
    type: round_trip
    provider: scrape
    origin: BOS
    destination: PEK
    destination_name: Beijing
    date_start: "2026-10-06"    # matches Asia Grand Tour's outbound window
    date_end: "2026-12-22"      # Dec 1 (last outbound day) + 21-day stay, so late
                                 # departures' returns aren't cut off
    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]   # all 7 days -- not a real
        # restriction; this is the mechanism _route_dates() uses to sample
        # outbound dates only through date_start..(date_end - max stay) while
        # still letting date_end extend further for valid returns. Without it,
        # outbound sampling and the return-date cutoff share one field and
        # can't be set independently (verified against code/searcher.py's
        # _route_dates before relying on this).
    sample_dates: 61            # >= (Dec 1 - Oct 6).days + 1 -> every single day
                                 # in the window sampled, no gaps
    stay_min: 21
    stay_max: 21                # fixed 3-week stay only
    max_stops: 1   # show both nonstop and 1-stop live panels
    # Date window moved to match Asia Grand Tour's on 2026-07-23; provider
    # changed ignav->scrape same day -- see
    # docs/superpowers/specs/2026-07-23-scraping-rewrite-design.md.
    # Narrowed to PEK-only (dropped PVG/Shanghai, HKG/Hong Kong) and to daily
    # Sep-Oct sampling on 2026-07-22: a coarser 8-date sample across 3
    # destinations missed a real $944 Hainan BOS-PEK fare on 2026-09-30 (found
    # by manually querying Ignav) because that exact date wasn't one of the 8
    # sampled -- the nearest sampled date (Sep 27) priced Hainan at $1,133
    # instead. Daily PEK-only sampling closes that gap directly.
    #
    # A dedicated "Hainan Direct BOS-PEK" SerpAPI route lived here until
    # 2026-07-22, added on the belief that "Ignav carries no Hainan fares at
    # all for this route." That was verified WRONG by querying Ignav's API
    # directly: it returned Hainan flight HU730/729 (Boeing 787, BOS-PEK) as a
    # single correctly-nonstop segment, at prices matching SerpAPI exactly
    # ($1,106 for Oct21->Nov11, $944 for Sep30->Oct21). Retired as redundant —
    # this route already covers Hainan via Ignav (free) with no loss of
    # correctness. See git history for the removed route if ever needed again.

search:
  provider: scrape
  date_start: "2026-10-06"
  date_end: "2026-12-29"
  sample_dates: 4
  alert_threshold: 0.10
  cache_hours: 6
  market: US
  cabin_class: economy
```

- [ ] **Step 2: Verify the new config produces the intended outbound sampling window for both routes**

```bash
cd ~/projects/claude/flight-bot && source .venv/bin/activate && python3 -c "
import yaml
from code.searcher import _route_dates
cfg = yaml.safe_load(open('config/routes.yaml'))
for route in cfg['routes']:
    dates, date_end = _route_dates(route, cfg['search'])
    print(route['name'], '-> n dates:', len(dates), 'first:', dates[0], 'last:', dates[-1], 'return cutoff:', date_end)
"
```
Expected: both routes show `first: 2026-10-06` and `last: 2026-12-01` (or the nearest Tue/Thu to those, for Asia Grand Tour; every single day for Beijing since it samples all 7 weekdays daily)

- [ ] **Step 3: Archive old data**

```bash
cd ~/projects/claude/flight-bot
mkdir -p data/archive
mv data/prices.csv data/archive/prices_serpapi_2026-05-03_to_2026-07-22.csv
mv data/round_trip_prices.csv data/archive/round_trip_prices_ignav_2026-05-03_to_2026-07-22.csv
ls data/ data/archive/
```
Expected: `data/` no longer has `prices.csv`/`round_trip_prices.csv`; `data/archive/` has both, renamed

- [ ] **Step 4: Check `.gitignore` covers the archive directory the same way it covers `data/*.csv`**

```bash
grep -n "data/\*\.csv\|data/archive" .gitignore
```
If `data/archive/` is not already covered (the pattern `data/*.csv` only matches one directory level, same gotcha jal-bot hit), add a line:

```bash
echo "data/archive/" >> .gitignore
```

- [ ] **Step 5: Commit**

```bash
git add config/routes.yaml .gitignore
git commit -m "Switch both routes to scrape provider; Asia Grand Tour KIX->SHA; align date windows; archive old API-era data"
```

---

### Task 6: Dependencies, timer cadence, docs

**Files:**
- Modify: `requirements.txt`
- Modify: `flight-bot.timer`
- Modify: `CLAUDE.md`
- Modify: `README.md`

**Interfaces:**
- None (packaging/ops/docs only).

- [ ] **Step 1: Add Playwright to `requirements.txt`**

Add this line to `requirements.txt` (matching jal-bot's pin):

```
playwright>=1.47
```

- [ ] **Step 2: Install it**

```bash
cd ~/projects/claude/flight-bot && source .venv/bin/activate && uv pip install playwright>=1.47
python -c "import playwright; print('ok')"
```
Expected: `ok` (no `playwright install` needed — `browser.py` connects to a system `google-chrome` over CDP, not Playwright's own bundled browser)

- [ ] **Step 3: Update `flight-bot.timer`**

Replace its contents with:

```
[Unit]
Description=Run Flight Price Bot daily at 09:30

[Timer]
OnCalendar=*-*-* 09:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Install the updated timer**

```bash
cp ~/projects/claude/flight-bot/flight-bot.timer ~/.config/systemd/user/flight-bot.timer
systemctl --user daemon-reload
systemctl --user restart flight-bot.timer
systemctl --user list-timers 'flight-bot*' --no-pager
```
Expected: `NEXT` shows tomorrow (or later today) at 09:30, not 6 days out

- [ ] **Step 5: Update `CLAUDE.md`**

Add a new dated section near the top (after the `# flight-bot` header, before `## Setup`):

```markdown
## Architecture update (2026-07-23 — scrape is now the default provider)

Both routes now default to `provider: scrape` — jal-bot's proven, free
Google-Flights-scraping technique (headful Akamai-defeating Chrome + a
from-scratch deep-link protobuf encoder + aria-label parser), vendored into
`code/browser.py`/`code/gflights.py`/`code/gflights_searcher.py`. SerpAPI and
Ignav remain available as an explicit per-route fallback (`provider: serpapi`
or `provider: ignav`) — not removed, just no longer the default. See
`docs/superpowers/specs/2026-07-23-scraping-rewrite-design.md` for the full
design and `docs/superpowers/plans/2026-07-23-scraping-rewrite.md` for how it
was implemented.

- **Why:** flight-bot used a paid, quota-limited API since its first commit
  (2026-05-03) because no scraping alternative existed anywhere in this
  workspace at the time. jal-bot built one later (for a different problem —
  JAL's own Akamai-gated site) and it turned out to also work directly
  against Google Flights, for free, with no bot-gate hit in practice.
- **What's lost:** scraping only exposes total price + the first leg's detail
  (airline/stops/duration/one layover) per fare card, not full per-direction
  segment detail the way SerpAPI/Ignav gave it. Scrape-sourced CSV rows have a
  simpler one-line `details` field; the dashboard's live-panel segment
  timeline falls back to showing just the airline name for these rows
  (`code/html_writer.py:_render_stop_panel`'s existing no-structured-segments
  fallback — unchanged code, already handled this case).
- **Route shape changed too**: Asia Grand Tour's middle segment moved from
  KIX→HKG to KIX→SHA (Shanghai), with SHA→HKG self-arranged/untracked (same
  convention as the pre-existing untracked NRT→KIX gap).
- **Timer moved from every-6-days to daily** (`OnCalendar=*-*-* 09:30:00`) —
  the 6-day cadence was SerpAPI-quota-driven (250 searches/month); no longer
  applicable once scraping is the default.
- **Old data archived, not migrated**: `data/prices.csv`/`round_trip_prices.csv`
  (SerpAPI/Ignav-era, old Sep-Oct date window) moved to `data/archive/` — both
  the CSV `details` shape and the date window changed at once, so reconciling
  old and new rows in one file wasn't worth it.
```

- [ ] **Step 6: Update `README.md`**

In the `## Routes` section, add a note after the existing bullet points:

```markdown
- **`provider: scrape`** is the new default (2026-07-23) — free, no quota, no
  API key needed; scrapes Google Flights directly via a headful Chrome
  (`code/browser.py`) and a deep-link URL (`code/gflights.py`). `serpapi`/
  `ignav` remain available as an explicit per-route fallback.
```

- [ ] **Step 7: Commit**

```bash
git add requirements.txt flight-bot.timer CLAUDE.md README.md
git commit -m "Docs + deps + timer cadence for the scrape-provider switch"
```

---

### Task 7: Live verification

**Files:** none (verification only)

- [ ] **Step 1: Direct search-function smoke test — Asia Grand Tour (no CSV/dashboard/deploy side effects)**

```bash
cd ~/projects/claude/flight-bot && source .venv/bin/activate && python3 -c "
import yaml
from code.searcher import search_multi_city
cfg = yaml.safe_load(open('config/routes.yaml'))
route = next(r for r in cfg['routes'] if r['name'] == 'Asia Grand Tour')
search_cfg = dict(cfg['search']); search_cfg.update(route.get('search', {})); search_cfg['provider'] = 'scrape'
search_cfg['sample_dates'] = 2   # keep this smoke test fast
result = search_multi_city(route, search_cfg)
for stops, offer in result.items():
    print(stops, offer.price if offer else None, offer.airline if offer else None)
"
```
Expected: real prices printed for `0` (nonstop) and/or `1` (one-stop), not exceptions. If a stop count comes back `None`, that's fine (means no fare of that type existed for the 2 sampled dates) — only a crash/traceback is a failure here.

- [ ] **Step 2: Direct search-function smoke test — Beijing**

```bash
cd ~/projects/claude/flight-bot && source .venv/bin/activate && python3 -c "
import yaml
from code.searcher import search_round_trip
cfg = yaml.safe_load(open('config/routes.yaml'))
route = next(r for r in cfg['routes'] if r['name'] == 'BOS ↔ Beijing (PEK)')
search_cfg = dict(cfg['search']); search_cfg.update(route.get('search', {})); search_cfg['provider'] = 'scrape'
search_cfg['sample_dates'] = 2
result = search_round_trip(route, search_cfg)
for stops, offer in result.items():
    print(stops, offer.price if offer else None, offer.airline if offer else None)
"
```
Expected: same — real prices or a clean `None`, no traceback

- [ ] **Step 3: STOP before running the full pipeline**

`main.py`'s `_deploy()` function automatically `git commit`s and `git push`es `site/index.html` to the public `flight-bot-site` gh-pages repo whenever it changes. Do **not** run `python main.py` (or restart `flight-bot.service`) as part of automated verification — that pushes to a public site. Ask the user for explicit confirmation before running a real full pipeline run, exactly as was done for jal-bot's equivalent step.

- [ ] **Step 4: (Only after explicit user confirmation) full pipeline run**

```bash
cd ~/projects/claude/flight-bot && source .venv/bin/activate && python main.py
cat output/latest.txt
```
Expected: both routes searched, prices written to `data/prices.csv`/`data/round_trip_prices.csv` with the new (simpler, one-line) `details` shape, `output/listings.html`/`site/index.html` rebuilt, `output/latest.txt` shows a real report. Confirm `data/prices.csv`/`data/round_trip_prices.csv` have fresh rows before considering this done.

- [ ] **Step 5: Full test suite one more time**

```bash
python -m pytest -q
```
Expected: same single pre-existing unrelated failure, everything else green

---

## Self-Review

**Spec coverage:**
- Both routes ported to scrape ✓ (Task 4, 5)
- Beijing date window matched, with correct `date_end` math ✓ (Task 5)
- SerpAPI/Ignav kept as fallback, not removed ✓ (Task 4's dispatch still has explicit `ignav`/`serpapi` branches; nothing deleted)
- Route shape change (KIX→SHA) ✓ (Task 5)
- Total price + first-leg detail only, dashboard degrades gracefully ✓ (Task 4's `_scrape_build_offer`, documented fallback in `html_writer.py` — confirmed by reading that file's existing `_render_stop_panel`/`_parse_legs`, no code changes needed there)
- Daily timer ✓ (Task 6)
- Docs updated ✓ (Task 6)
- Live verification ✓ (Task 7), with the public-site-push risk explicitly gated behind user confirmation

**Correction from the approved spec, found while writing this plan:** the spec claimed the CSV schema needs a 3rd date column for Asia Grand Tour's 3 legs. That's wrong — re-reading `code/notifier.py`'s existing `_CSV_FIELDS` and `code/searcher.py`'s existing `_combine_legs` (used by the current SerpAPI/Ignav multi-city path) shows multi-city trips already collapse to just `departure_date` (first leg) + `final_leg_date` (last leg), with the middle leg only appearing inside the `details` text. No CSV column changes are needed. This plan's `FlightOffer`/CSV usage matches that existing convention exactly — flagging the discrepancy here rather than silently fixing the spec doc.

**Placeholder scan:** none found — every step has complete code, exact commands, and expected output.

**Type consistency:** `_scrape_offers_by_stops`/`_scrape_build_offer`/`search_round_trip_scrape`/`search_multi_city_scrape` names and signatures are consistent between Task 4's test mocks (`code.searcher.scrape_search_all_options`, `code.searcher.launch_browser`) and its implementation steps. `FlightOffer` fields used (`price, currency, departure_date, final_leg_date, stops, airline, details`) match the dataclass already defined earlier in `code/searcher.py` — no new fields introduced, so `notifier.py`/`html_writer.py`/`main.py` need no changes, confirmed by having read all three files in full during planning.
