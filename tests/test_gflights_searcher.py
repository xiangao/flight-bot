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
