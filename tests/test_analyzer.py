import csv
import pytest
from pathlib import Path
from datetime import datetime, timedelta
from code.analyzer import analyze, load_recent_prices


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "timestamp", "route", "cheapest_price", "currency",
            "departure_date", "final_leg_date", "stops", "airline",
        ])
        writer.writeheader()
        writer.writerows(rows)


def _make_row(route: str, price: float, days_ago: int) -> dict:
    ts = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return {
        "timestamp": ts, "route": route, "cheapest_price": price,
        "currency": "USD", "departure_date": "2026-09-15",
        "final_leg_date": "2026-10-06", "stops": 1, "airline": "JAL",
    }


def test_missing_csv_always_alerts(tmp_path):
    result = analyze(tmp_path / "prices.csv", "Route A", 1200.0)
    assert result.should_alert is True


def test_fewer_than_7_rows_always_alerts(tmp_path):
    csv_path = tmp_path / "prices.csv"
    rows = [_make_row("Route A", 1000.0, i) for i in range(5)]
    _write_csv(csv_path, rows)
    result = analyze(csv_path, "Route A", 900.0)
    assert result.should_alert is True


def test_price_10pct_below_avg_alerts(tmp_path):
    csv_path = tmp_path / "prices.csv"
    rows = [_make_row("Route A", 1000.0, i) for i in range(7)]
    _write_csv(csv_path, rows)
    result = analyze(csv_path, "Route A", 850.0)
    assert result.should_alert is True
    assert result.pct_below == pytest.approx(0.15, abs=0.01)
    assert result.avg_price == pytest.approx(1000.0, abs=1.0)


def test_price_at_avg_does_not_alert(tmp_path):
    csv_path = tmp_path / "prices.csv"
    rows = [_make_row("Route A", 1000.0, i) for i in range(7)]
    _write_csv(csv_path, rows)
    result = analyze(csv_path, "Route A", 1000.0)
    assert result.should_alert is False


def test_price_5pct_below_does_not_alert(tmp_path):
    csv_path = tmp_path / "prices.csv"
    rows = [_make_row("Route A", 1000.0, i) for i in range(7)]
    _write_csv(csv_path, rows)
    result = analyze(csv_path, "Route A", 950.0)
    assert result.should_alert is False


def test_different_routes_isolated(tmp_path):
    csv_path = tmp_path / "prices.csv"
    rows = [_make_row("Route A", 1000.0, i) for i in range(7)]
    _write_csv(csv_path, rows)
    result = analyze(csv_path, "Route B", 500.0)
    assert result.should_alert is True


def test_old_prices_outside_7day_window_ignored(tmp_path):
    csv_path = tmp_path / "prices.csv"
    # 7 recent rows at 1000, plus 3 old rows at 100 (should be excluded)
    rows = [_make_row("Route A", 1000.0, i) for i in range(7)]
    rows += [_make_row("Route A", 100.0, i) for i in range(10, 13)]
    _write_csv(csv_path, rows)
    result = analyze(csv_path, "Route A", 1000.0)
    assert result.avg_price == pytest.approx(1000.0, abs=1.0)
    assert result.should_alert is False
