import pytest
from datetime import date
from unittest.mock import MagicMock, patch
from code.searcher import (
    _sample_dates,
    _cheapest_offer,
    _ignav_cheapest_offer,
    search_round_trip,
    search_multi_city,
    FlightOffer,
)


def _serpapi_response(price: int, dep_at: str = "2026-09-15 10:00", carrier: str = "JAL") -> dict:
    return {
        "best_flights": [
            {
                "flights": [
                    {
                        "departure_airport": {"id": "BOS", "time": dep_at},
                        "arrival_airport": {"id": "HKG", "time": "2026-09-16 14:00"},
                        "airline": carrier,
                        "duration": 900,
                    }
                ],
                "layovers": [],
                "price": price,
                "total_duration": 900,
                "type": "Round trip",
            }
        ]
    }


def _serpapi_multicity_response(price: int, dep_at: str = "2026-09-15 13:00") -> dict:
    return {
        "best_flights": [
            {
                "flights": [
                    {
                        "departure_airport": {"id": "BOS", "time": dep_at},
                        "arrival_airport": {"id": "NRT", "time": "2026-09-16 16:00"},
                        "airline": "JAL",
                        "duration": 840,
                    }
                ],
                "layovers": [],
                "price": price,
                "total_duration": 840,
                "type": "Multi-city",
            }
        ]
    }


def _ignav_response(price: int, dep_at: str = "2026-09-15T10:00:00", carrier: str = "JAL") -> dict:
    return {
        "origin": "BOS",
        "destination": "HKG",
        "departure_date": dep_at[:10],
        "return_date": None,
        "itineraries": [
            {
                "price": {"amount": price, "currency": "USD"},
                "outbound": {
                    "carrier": carrier,
                    "duration_minutes": 900,
                    "segments": [
                        {
                            "marketing_carrier_code": "JL",
                            "flight_number": "7",
                            "operating_carrier_name": carrier,
                            "departure_airport": "BOS",
                            "departure_time_local": dep_at,
                            "departure_timezone": "America/New_York",
                            "departure_time_utc": "2026-09-15T14:00:00Z",
                            "arrival_airport": "HKG",
                            "arrival_time_local": "2026-09-16T16:00:00",
                            "arrival_timezone": "Asia/Hong_Kong",
                            "arrival_time_utc": "2026-09-16T08:00:00Z",
                            "duration_minutes": 900,
                            "aircraft": None,
                        }
                    ],
                },
                "inbound": None,
                "cabin_class": "economy",
                "bags": None,
                "requires_self_transfer": False,
                "ignav_id": "ignav_test",
            }
        ],
    }


def test_sample_dates_returns_n_dates():
    dates = _sample_dates("2026-09-01", "2026-11-30", 4)
    assert len(dates) == 4


def test_sample_dates_span_full_window():
    dates = _sample_dates("2026-09-01", "2026-11-30", 4)
    assert dates[0] == date(2026, 9, 1)
    assert dates[-1] == date(2026, 11, 30)


def test_sample_dates_are_sorted():
    dates = _sample_dates("2026-09-01", "2026-11-30", 4)
    assert dates == sorted(dates)


def test_cheapest_offer_returns_lowest_price():
    data = {
        "best_flights": [
            {"flights": [{"departure_airport": {"id": "BOS", "time": "2026-09-15 10:00"}, "arrival_airport": {"id": "HKG"}, "airline": "CX"}], "layovers": [], "price": 1500},
            {"flights": [{"departure_airport": {"id": "BOS", "time": "2026-09-15 12:00"}, "arrival_airport": {"id": "HKG"}, "airline": "JAL"}], "layovers": [], "price": 1200},
        ]
    }
    result = _cheapest_offer(data)
    assert result.price == 1200.0
    assert result.airline == "JAL"


def test_cheapest_offer_excludes_turkish_airlines():
    data = {
        "best_flights": [
            {"flights": [{"departure_airport": {"id": "BOS", "time": "2026-09-15 10:00"}, "arrival_airport": {"id": "HKG"}, "airline": "Turkish Airlines"}], "layovers": [], "price": 900},
            {"flights": [{"departure_airport": {"id": "BOS", "time": "2026-09-15 12:00"}, "arrival_airport": {"id": "HKG"}, "airline": "JAL"}], "layovers": [], "price": 1200},
        ]
    }
    result = _cheapest_offer(data)
    assert result.price == 1200.0
    assert result.airline == "JAL"


def test_cheapest_offer_returns_none_for_empty():
    assert _cheapest_offer({}) is None
    assert _cheapest_offer({"best_flights": [], "other_flights": []}) is None


def test_cheapest_offer_counts_layovers_as_stops():
    data = {
        "best_flights": [
            {
                "flights": [{"departure_airport": {"id": "BOS", "time": "2026-09-15 08:00"}, "arrival_airport": {"id": "HKG"}, "airline": "TK"}],
                "layovers": [{"name": "Istanbul Airport", "duration": 90, "id": "IST"}],
                "price": 1100,
            }
        ]
    }
    result = _cheapest_offer(data)
    assert result.stops == 1


def test_cheapest_offer_includes_segment_details():
    result = _cheapest_offer(_serpapi_response(1200, dep_at="2026-09-15 10:00"))
    assert "Outbound" in result.details
    assert "BOS" in result.details
    assert "HKG" in result.details


def test_cheapest_offer_parses_departure_date():
    result = _cheapest_offer(_serpapi_response(1200, dep_at="2026-09-15 10:00"))
    assert result.departure_date == "2026-09-15"


def test_search_round_trip_returns_cheapest(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.json.return_value = _serpapi_response(1100)
    mock_resp.raise_for_status.return_value = None
    monkeypatch.setattr("code.searcher.requests.get", lambda *a, **kw: mock_resp)
    monkeypatch.setattr("code.searcher._api_key", lambda: "testkey")

    route = {"origin": "BOS", "destination": "HKG", "stay_min": 18, "stay_max": 25, "max_stops": 1}
    config = {"date_start": "2026-09-01", "date_end": "2026-11-30", "sample_dates": 2}
    result = search_round_trip(route, config)
    assert result is not None
    assert result.price == 1100.0


def test_search_round_trip_skips_errors(monkeypatch):
    monkeypatch.setattr("code.searcher.requests.get", lambda *a, **kw: (_ for _ in ()).throw(Exception("timeout")))
    monkeypatch.setattr("code.searcher._api_key", lambda: "testkey")

    route = {"origin": "BOS", "destination": "HKG", "stay_min": 18, "stay_max": 25, "max_stops": 1}
    config = {"date_start": "2026-09-01", "date_end": "2026-09-05", "sample_dates": 2}
    result = search_round_trip(route, config)
    assert result is None


def test_search_multi_city_returns_cheapest(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.json.return_value = _serpapi_multicity_response(1700)
    mock_resp.raise_for_status.return_value = None
    monkeypatch.setattr("code.searcher.requests.get", lambda *a, **kw: mock_resp)
    monkeypatch.setattr("code.searcher._api_key", lambda: "testkey")

    route = {
        "segments": [
            {"origin": "BOS", "destination": "NRT", "stay_min": 7, "stay_max": 10},
            {"origin": "KIX", "destination": "HKG", "stay_min": 14, "stay_max": 18},
            {"origin": "HKG", "destination": "BOS"},
        ],
        "max_stops": 1,
    }
    config = {"date_start": "2026-09-01", "date_end": "2026-11-30", "sample_dates": 2}
    result = search_multi_city(route, config)
    assert result is not None
    assert result.price == 1700.0


def test_search_multi_city_sets_final_leg_date(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.json.return_value = _serpapi_multicity_response(1700)
    mock_resp.raise_for_status.return_value = None
    monkeypatch.setattr("code.searcher.requests.get", lambda *a, **kw: mock_resp)
    monkeypatch.setattr("code.searcher._api_key", lambda: "testkey")

    route = {
        "segments": [
            {"origin": "BOS", "destination": "NRT", "stay_min": 7, "stay_max": 7},
            {"origin": "KIX", "destination": "HKG", "stay_min": 14, "stay_max": 14},
            {"origin": "HKG", "destination": "BOS"},
        ],
        "max_stops": 1,
    }
    config = {"date_start": "2026-09-01", "date_end": "2026-11-30", "sample_dates": 2}
    result = search_multi_city(route, config)
    # dep_date=2026-09-01, stay1=7 → mid=2026-09-08, stay2=14 → ret=2026-09-22
    assert result.final_leg_date == "2026-09-22"


def test_ignav_cheapest_offer_parses_price_and_departure_date():
    result = _ignav_cheapest_offer(_ignav_response(1300, dep_at="2026-09-20T09:30:00"))
    assert result is not None
    assert result.price == 1300.0
    assert result.departure_date == "2026-09-20"
    assert result.airline == "JAL"
    assert "Outbound" in result.details
    assert "BOS" in result.details
    assert "HKG" in result.details


def test_search_round_trip_ignav_posts_round_trip_payload(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.json.return_value = _ignav_response(1100)
    mock_resp.raise_for_status.return_value = None
    monkeypatch.setattr("code.searcher.requests.post", lambda *a, **kw: mock_resp)
    monkeypatch.setattr("code.searcher._ignav_api_key", lambda: "testkey")
    monkeypatch.setattr("code.searcher._read_cache", lambda *a, **kw: None)
    monkeypatch.setattr("code.searcher._write_cache", lambda *a, **kw: None)

    route = {"origin": "BOS", "destination": "HKG", "stay_min": 18, "stay_max": 18, "max_stops": 1}
    config = {"provider": "ignav", "date_start": "2026-09-01", "date_end": "2026-11-30", "sample_dates": 1}
    result = search_round_trip(route, config, stops_filter=1)
    assert result is not None
    assert result.price == 1100.0


def test_search_round_trip_ignav_selects_cheapest_destination(monkeypatch):
    seen_destinations = []
    prices = {"PEK": 1200, "PVG": 1000, "HKG": 1100}

    def fake_post(endpoint, payload, config):
        assert endpoint == "/fares/round-trip"
        seen_destinations.append(payload["destination"])
        return _ignav_response(prices[payload["destination"]])

    monkeypatch.setattr("code.searcher._ignav_post", fake_post)

    route = {
        "origin": "BOS",
        "destinations": [
            {"code": "PEK", "name": "Beijing"},
            {"code": "PVG", "name": "Shanghai"},
            {"code": "HKG", "name": "Hong Kong"},
        ],
        "stay_min": 21,
        "stay_max": 21,
        "max_stops": 1,
    }
    config = {"provider": "ignav", "date_start": "2026-09-01", "date_end": "2026-11-30", "sample_dates": 1}
    result = search_round_trip(route, config, stops_filter=1)
    assert result is not None
    assert result.price == 1000.0
    assert result.final_leg_date == "2026-09-22"
    assert "Shanghai" in result.airline
    assert seen_destinations == ["PEK", "PVG", "HKG"]


def test_search_round_trip_ignav_uses_flexible_stay_step(monkeypatch):
    seen_return_dates = []

    def fake_post(endpoint, payload, config):
        seen_return_dates.append(payload["return_date"])
        return _ignav_response(1000)

    monkeypatch.setattr("code.searcher._ignav_post", fake_post)
    route = {
        "origin": "BOS",
        "destination": "HKG",
        "stay_min": 18,
        "stay_max": 22,
        "stay_step": 2,
        "max_stops": 1,
    }
    config = {"provider": "ignav", "date_start": "2026-09-01", "date_end": "2026-11-30", "sample_dates": 1}
    result = search_round_trip(route, config, stops_filter=1)
    assert result is not None
    assert seen_return_dates == ["2026-09-19", "2026-09-21", "2026-09-23"]


def test_search_multi_city_ignav_sums_one_way_legs(monkeypatch):
    prices = [700, 300, 800]

    def fake_post(endpoint, payload, config):
        assert endpoint == "/fares/one-way"
        return _ignav_response(prices.pop(0), dep_at=f"{payload['departure_date']}T10:00:00")

    monkeypatch.setattr("code.searcher._ignav_post", fake_post)
    route = {
        "segments": [
            {"origin": "BOS", "destination": "NRT", "stay_min": 7, "stay_max": 7},
            {"origin": "KIX", "destination": "HKG", "stay_min": 14, "stay_max": 14},
            {"origin": "HKG", "destination": "BOS"},
        ],
        "max_stops": 1,
    }
    config = {"provider": "ignav", "date_start": "2026-09-01", "date_end": "2026-11-30", "sample_dates": 1}
    result = search_multi_city(route, config, stops_filter=1)
    assert result is not None
    assert result.price == 1800.0
    assert result.final_leg_date == "2026-09-22"
