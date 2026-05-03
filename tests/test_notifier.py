import csv
import pytest
from pathlib import Path
from unittest.mock import patch, call
from code.notifier import FlightResult, append_to_csv, write_summary, send_desktop_notification
from code.analyzer import AlertResult


def _result(route="Test Route", price=1500.0) -> FlightResult:
    return FlightResult(
        route=route, cheapest_price=price, currency="USD",
        departure_date="2026-09-15", final_leg_date="2026-10-06",
        stops=1, airline="JAL",
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
    assert "ALERT" in content


def test_write_summary_no_alert_marker_when_not_alerting(tmp_path):
    out = tmp_path / "latest.txt"
    write_summary(out, [_result()], [_alert(should_alert=False)])
    content = out.read_text()
    assert "ALERT" not in content


def test_send_notification_calls_notify_send():
    with patch("shutil.which", return_value="/usr/bin/notify-send"):
        with patch("subprocess.run") as mock_run:
            send_desktop_notification(_result(), _alert())
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "notify-send"
            assert "Test Route" in cmd[1]
            assert "1,500" in cmd[2]


def test_send_notification_skips_when_notify_send_missing():
    with patch("shutil.which", return_value=None):
        with patch("subprocess.run") as mock_run:
            send_desktop_notification(_result(), _alert())
            mock_run.assert_not_called()
