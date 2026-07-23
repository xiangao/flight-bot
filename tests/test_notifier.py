import csv
import pytest
from pathlib import Path
from unittest.mock import patch
from code.notifier import FlightResult, append_to_csv, write_summary, send_desktop_notification
from code.analyzer import AlertResult


def _result(route="Test Route", price=1500.0) -> FlightResult:
    return FlightResult(
        route=route, cheapest_price=price, currency="USD",
        departure_date="2026-09-15", final_leg_date="2026-10-06",
        stops=1, airline="JAL", details="Outbound: JAL, 1 stop, 15h\n  BOS -> HKG",
    )


def _alert(should_alert=True, avg=1700.0, pct_below=0.12) -> AlertResult:
    return AlertResult(
        should_alert=should_alert, current_price=1500.0,
        avg_price=avg, pct_below=pct_below,
    )


def test_append_creates_csv_with_header(tmp_path):
    csv_path = tmp_path / "prices.csv"
    append_to_csv(csv_path, _result())
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["route"] == "Test Route"
    assert rows[0]["cheapest_price"] == "1500.0"
    assert "Outbound" in rows[0]["details"]
    assert "timestamp" in rows[0]


def test_append_adds_second_row_without_duplicate_header(tmp_path):
    csv_path = tmp_path / "prices.csv"
    append_to_csv(csv_path, _result(price=1500.0))
    append_to_csv(csv_path, _result(price=1400.0))
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[1]["cheapest_price"] == "1400.0"


def test_append_creates_parent_dirs(tmp_path):
    csv_path = tmp_path / "nested" / "dir" / "prices.csv"
    append_to_csv(csv_path, _result())
    assert csv_path.exists()


def test_write_summary_contains_route_and_price(tmp_path):
    out = tmp_path / "latest.txt"
    write_summary(out, [_result()], [_alert()])
    content = out.read_text()
    assert "Test Route" in content
    assert "1,500.00" in content
    assert "Details:" in content
    assert "Outbound" in content
    assert "ALERT" in content


def test_write_summary_no_alert_marker_when_not_alerting(tmp_path):
    out = tmp_path / "latest.txt"
    write_summary(out, [_result()], [_alert(should_alert=False)])
    content = out.read_text()
    assert "ALERT" not in content


def test_send_notification_calls_notify_send():
    with patch("code.notifier.shutil.which", return_value="/usr/bin/notify-send"):
        with patch("subprocess.run") as mock_run:
            send_desktop_notification(_result(), _alert())
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "notify-send"
            assert "Test Route" in cmd[1]
            assert "1,500" in cmd[2]


def test_send_notification_skips_when_notify_send_missing():
    with patch("code.notifier.shutil.which", return_value=None):
        with patch("subprocess.run") as mock_run:
            send_desktop_notification(_result(), _alert())
            mock_run.assert_not_called()


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
