import pytest
from datetime import date
from unittest.mock import MagicMock
from code.searcher import _sample_dates, _extract_cheapest, search_round_trip, search_multi_city


def _offer(price: str, dep_at: str = "2026-09-15T10:00:00", carrier: str = "JL") -> dict:
    return {
        "price": {"grandTotal": price, "currency": "USD"},
        "itineraries": [
            {"segments": [
                {"departure": {"at": dep_at}, "arrival": {"at": "2026-09-16T15:00:00"},
                 "carrierCode": carrier},
            ]},
            {"segments": [
                {"departure": {"at": "2026-10-06T23:00:00"}, "arrival": {"at": "2026-10-07T16:00:00"},
                 "carrierCode": carrier},
            ]},
        ],
    }


def _offer_with_stop(price: str) -> dict:
    offer = _offer(price)
    offer["itineraries"][0]["segments"].append({
        "departure": {"at": "2026-09-16T08:00:00"}, "arrival": {"at": "2026-09-16T20:00:00"},
        "carrierCode": "NH",
    })
    return offer


def test_sample_dates_returns_n_dates():
    dates = _sample_dates("2026-09-01", "2026-11-30", 8)
    assert len(dates) == 8


def test_sample_dates_span_full_window():
    dates = _sample_dates("2026-09-01", "2026-11-30", 8)
    assert dates[0] == date(2026, 9, 1)
    assert dates[-1] == date(2026, 11, 30)


def test_sample_dates_are_sorted():
    dates = _sample_dates("2026-09-01", "2026-11-30", 8)
    assert dates == sorted(dates)


def test_extract_cheapest_returns_lowest_price():
    offers = [_offer("1500.00"), _offer("1200.00"), _offer("1800.00")]
    result = _extract_cheapest(offers, max_stops_per_leg=1)
    assert result.price == 1200.0


def test_extract_cheapest_returns_none_for_empty():
    assert _extract_cheapest([], max_stops_per_leg=1) is None


def test_extract_cheapest_filters_excess_stops():
    offers = [_offer_with_stop("900.00")]  # 2 segments in leg 0 = 1 stop = ok with max 1
    result = _extract_cheapest(offers, max_stops_per_leg=1)
    assert result is not None
    assert result.stops == 1


def test_extract_cheapest_rejects_too_many_stops():
    offer = _offer_with_stop("900.00")
    # Add a third segment to push to 2 stops
    offer["itineraries"][0]["segments"].append({
        "departure": {"at": "2026-09-16T22:00:00"}, "arrival": {"at": "2026-09-17T12:00:00"},
        "carrierCode": "CA",
    })
    result = _extract_cheapest([offer], max_stops_per_leg=1)
    assert result is None


def test_extract_cheapest_departure_date():
    result = _extract_cheapest([_offer("1200.00", dep_at="2026-09-15T10:00:00")], max_stops_per_leg=1)
    assert result.departure_date == "2026-09-15"


def test_search_round_trip_returns_cheapest(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.data = [_offer("1300.00"), _offer("1100.00")]
    mock_client = MagicMock()
    mock_client.shopping.flight_offers_search.get.return_value = mock_resp
    monkeypatch.setattr("code.searcher._make_client", lambda: mock_client)

    route = {"origin": "BOS", "destination": "HKG", "stay_min": 18, "stay_max": 25, "max_stops": 1}
    config = {"date_start": "2026-09-01", "date_end": "2026-11-30", "sample_dates": 2}
    result = search_round_trip(route, config)
    assert result is not None
    assert result.price == 1100.0


def test_search_round_trip_skips_amadeus_errors(monkeypatch):
    from amadeus import ResponseError
    mock_client = MagicMock()
    mock_client.shopping.flight_offers_search.get.side_effect = ResponseError(
        MagicMock(status_code=500, result={"errors": [{"detail": "Internal error"}]})
    )
    monkeypatch.setattr("code.searcher._make_client", lambda: mock_client)

    route = {"origin": "BOS", "destination": "HKG", "stay_min": 18, "stay_max": 25, "max_stops": 1}
    config = {"date_start": "2026-09-01", "date_end": "2026-09-05", "sample_dates": 2}
    result = search_round_trip(route, config)
    assert result is None  # All calls failed, no crash


def test_search_multi_city_returns_cheapest(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.data = [_offer("2800.00"), _offer("2500.00")]
    mock_client = MagicMock()
    mock_client.shopping.flight_offers_search.post.return_value = mock_resp
    monkeypatch.setattr("code.searcher._make_client", lambda: mock_client)

    route = {
        "segments": [
            {"origin": "BOS", "destination": "TYO", "stay_min": 7, "stay_max": 10},
            {"origin": "OSA", "destination": "HKG", "stay_min": 14, "stay_max": 18},
            {"origin": "HKG", "destination": "BOS"},
        ],
        "max_stops": 1,
    }
    config = {"date_start": "2026-09-01", "date_end": "2026-11-30", "sample_dates": 2}
    result = search_multi_city(route, config)
    assert result is not None
    assert result.price == 2500.0
