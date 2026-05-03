# Flight Price Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python bot that runs on machine startup, searches Amadeus for the cheapest prices on two configured routes, logs results to CSV, and fires a desktop notification when price drops below the rolling average.

**Architecture:** A thin `main.py` orchestrator calls three focused modules — `searcher.py` (Amadeus API), `analyzer.py` (rolling average logic), and `notifier.py` (CSV + desktop alert). A `systemd` user service triggers the script after network comes up on login.

**Tech Stack:** Python 3.11+, Amadeus Python SDK v9, python-dotenv, PyYAML, pytest, notify-send (Linux desktop), systemd user services

---

## File Map

| File | Responsibility |
|------|---------------|
| `config/routes.yaml` | Route definitions, date window, alert threshold |
| `code/__init__.py` | Empty — marks `code/` as a Python package |
| `code/analyzer.py` | Load CSV, compute 7-day rolling average, return alert decision |
| `code/notifier.py` | Append to CSV, write latest.txt, fire notify-send |
| `code/searcher.py` | Amadeus auth, round-trip and multi-city flight search |
| `main.py` | Orchestration: load config → search → analyze → notify |
| `tests/__init__.py` | Empty |
| `tests/test_analyzer.py` | Unit tests for rolling average and alert logic |
| `tests/test_notifier.py` | Unit tests for CSV writing and notification dispatch |
| `tests/test_searcher.py` | Unit tests for date sampling, offer parsing, search with mocked Amadeus client |
| `requirements.txt` | Python dependencies |
| `.env.example` | Template for Amadeus API credentials |
| `.gitignore` | Exclude `.env`, `.venv/`, `data/`, `output/` |
| `~/.config/systemd/user/flight-bot.service` | Systemd user service (outside repo) |

---

## Task 1: Project Scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `config/routes.yaml`
- Create: `code/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
cd /home/xao/projects/claude/flight-bot
mkdir -p config code tests data output
```

- [ ] **Step 2: Write requirements.txt**

```
amadeus>=9.0.0
python-dotenv>=1.0.0
pyyaml>=6.0
pytest>=8.0.0
```

- [ ] **Step 3: Write .gitignore**

```
.venv/
.env
data/
output/
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 4: Write .env.example**

```
AMADEUS_CLIENT_ID=your_client_id_here
AMADEUS_CLIENT_SECRET=your_client_secret_here
```

- [ ] **Step 5: Write config/routes.yaml**

```yaml
routes:
  - name: "Asia Grand Tour"
    type: multi_city
    segments:
      - origin: BOS
        destination: TYO
        stay_min: 7
        stay_max: 10
      - origin: OSA
        destination: HKG
        stay_min: 14
        stay_max: 18
      - origin: HKG
        destination: BOS
    max_stops: 1

  - name: "Boston-HongKong RT"
    type: round_trip
    origin: BOS
    destination: HKG
    stay_min: 18
    stay_max: 25
    max_stops: 1

search:
  date_start: "2026-09-01"
  date_end: "2026-11-30"
  sample_dates: 8
  alert_threshold: 0.10
```

- [ ] **Step 6: Create empty package init files**

Create `code/__init__.py` — empty file.
Create `tests/__init__.py` — empty file.

- [ ] **Step 7: Create virtualenv and install dependencies**

```bash
cd /home/xao/projects/claude/flight-bot
test -d .venv || python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Expected: All packages install without errors.

- [ ] **Step 8: Commit**

```bash
git add config/ code/__init__.py tests/__init__.py requirements.txt .gitignore .env.example
git commit -m "feat: project scaffolding and config"
```

---

## Task 2: analyzer.py — Rolling Average and Alert Logic

**Files:**
- Create: `tests/test_analyzer.py`
- Create: `code/analyzer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_analyzer.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/xao/projects/claude/flight-bot
source .venv/bin/activate
pytest tests/test_analyzer.py -v
```

Expected: `ImportError: cannot import name 'analyze' from 'code.analyzer'` (module doesn't exist yet).

- [ ] **Step 3: Implement code/analyzer.py**

```python
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


@dataclass
class AlertResult:
    should_alert: bool
    current_price: float
    avg_price: float
    pct_below: float  # fraction: 0.13 means 13% cheaper than avg; 0.0 if no history


def load_recent_prices(csv_path: Path, route_name: str, days: int = 7) -> list[float]:
    if not csv_path.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days)
    prices = []
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            if row["route"] != route_name:
                continue
            if datetime.fromisoformat(row["timestamp"]) >= cutoff:
                prices.append(float(row["cheapest_price"]))
    return prices


def analyze(
    csv_path: Path,
    route_name: str,
    current_price: float,
    threshold: float = 0.10,
) -> AlertResult:
    recent = load_recent_prices(csv_path, route_name)
    if len(recent) < 7:
        return AlertResult(
            should_alert=True,
            current_price=current_price,
            avg_price=0.0,
            pct_below=0.0,
        )
    avg = sum(recent) / len(recent)
    pct_below = (avg - current_price) / avg
    return AlertResult(
        should_alert=pct_below >= threshold,
        current_price=current_price,
        avg_price=avg,
        pct_below=pct_below,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_analyzer.py -v
```

Expected: 7 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add code/analyzer.py tests/test_analyzer.py
git commit -m "feat: analyzer — rolling average and alert logic"
```

---

## Task 3: notifier.py — CSV Writer and Desktop Alert

**Files:**
- Create: `tests/test_notifier.py`
- Create: `code/notifier.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_notifier.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_notifier.py -v
```

Expected: `ImportError: cannot import name 'FlightResult' from 'code.notifier'`.

- [ ] **Step 3: Implement code/notifier.py**

```python
import csv
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from code.analyzer import AlertResult


@dataclass
class FlightResult:
    route: str
    cheapest_price: float
    currency: str
    departure_date: str
    final_leg_date: str
    stops: int
    airline: str


_CSV_FIELDS = [
    "timestamp", "route", "cheapest_price", "currency",
    "departure_date", "final_leg_date", "stops", "airline",
]


def append_to_csv(csv_path: Path, result: FlightResult) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "route": result.route,
            "cheapest_price": result.cheapest_price,
            "currency": result.currency,
            "departure_date": result.departure_date,
            "final_leg_date": result.final_leg_date,
            "stops": result.stops,
            "airline": result.airline,
        })


def write_summary(
    output_path: Path,
    results: list[FlightResult],
    alerts: list[AlertResult],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"Flight Price Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
    for result, alert in zip(results, alerts):
        lines.append(f"\n{result.route}")
        lines.append(f"  Price:    ${result.cheapest_price:,.2f} {result.currency}")
        lines.append(f"  Dates:    {result.departure_date} → {result.final_leg_date}")
        lines.append(f"  Airline:  {result.airline}  |  Stops: {result.stops}")
        if alert.avg_price > 0:
            lines.append(
                f"  Avg(7d):  ${alert.avg_price:,.2f}  ({alert.pct_below * 100:+.1f}%)"
            )
        if alert.should_alert:
            lines.append("  ** ALERT: Notable price! **")
    output_path.write_text("\n".join(lines) + "\n")


def send_desktop_notification(result: FlightResult, alert: AlertResult) -> None:
    if not shutil.which("notify-send"):
        print("WARNING: notify-send not available, skipping desktop notification")
        return
    if alert.avg_price > 0:
        body = (
            f"${result.cheapest_price:,.0f} — {alert.pct_below * 100:.0f}% below"
            f" recent avg (${alert.avg_price:,.0f})\n"
            f"Best dates: {result.departure_date} → {result.final_leg_date}"
            f"  |  {result.airline}, {result.stops} stop(s)"
        )
    else:
        body = (
            f"${result.cheapest_price:,.0f} {result.currency}\n"
            f"Best dates: {result.departure_date} → {result.final_leg_date}"
            f"  |  {result.airline}, {result.stops} stop(s)"
        )
    subprocess.run(
        ["notify-send", f"✈ {result.route}", body, "--urgency=normal"],
        check=False,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_notifier.py -v
```

Expected: 7 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add code/notifier.py tests/test_notifier.py
git commit -m "feat: notifier — CSV writer and desktop alert"
```

---

## Task 4: searcher.py — Amadeus API Client

**Files:**
- Create: `tests/test_searcher.py`
- Create: `code/searcher.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_searcher.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_searcher.py -v
```

Expected: `ImportError: cannot import name '_sample_dates' from 'code.searcher'`.

- [ ] **Step 3: Implement code/searcher.py**

```python
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from amadeus import Client, ResponseError
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


@dataclass
class FlightOffer:
    price: float
    currency: str
    departure_date: str   # first leg departure date (YYYY-MM-DD)
    final_leg_date: str   # last leg departure date (YYYY-MM-DD)
    stops: int            # total connections across all legs
    airline: str          # slash-joined carrier codes


def _make_client() -> Client:
    return Client(
        client_id=os.environ["AMADEUS_CLIENT_ID"],
        client_secret=os.environ["AMADEUS_CLIENT_SECRET"],
    )


def _sample_dates(start: str, end: str, n: int) -> list[date]:
    d_start = date.fromisoformat(start)
    d_end = date.fromisoformat(end)
    span = (d_end - d_start).days
    return [d_start + timedelta(days=round(i * span / (n - 1))) for i in range(n)]


def _extract_cheapest(offers: list[dict], max_stops_per_leg: int) -> FlightOffer | None:
    valid = [
        o for o in offers
        if max(len(it["segments"]) - 1 for it in o["itineraries"]) <= max_stops_per_leg
    ]
    if not valid:
        return None
    cheapest = min(valid, key=lambda o: float(o["price"]["grandTotal"]))
    itins = cheapest["itineraries"]
    carriers = {seg["carrierCode"] for it in itins for seg in it["segments"]}
    return FlightOffer(
        price=float(cheapest["price"]["grandTotal"]),
        currency=cheapest["price"]["currency"],
        departure_date=itins[0]["segments"][0]["departure"]["at"][:10],
        final_leg_date=itins[-1]["segments"][0]["departure"]["at"][:10],
        stops=sum(len(it["segments"]) - 1 for it in itins),
        airline="/".join(sorted(carriers)),
    )


def search_round_trip(route: dict, config: dict) -> FlightOffer | None:
    client = _make_client()
    dates = _sample_dates(config["date_start"], config["date_end"], config["sample_dates"])
    date_end = date.fromisoformat(config["date_end"])
    best: FlightOffer | None = None

    for dep_date in dates:
        for stay in [route["stay_min"], route["stay_max"]]:
            ret_date = dep_date + timedelta(days=stay)
            if ret_date > date_end:
                continue
            try:
                resp = client.shopping.flight_offers_search.get(
                    originLocationCode=route["origin"],
                    destinationLocationCode=route["destination"],
                    departureDate=str(dep_date),
                    returnDate=str(ret_date),
                    adults=1,
                    max=5,
                    currencyCode="USD",
                )
                offer = _extract_cheapest(resp.data, route["max_stops"])
                if offer and (best is None or offer.price < best.price):
                    best = offer
            except ResponseError as e:
                print(f"WARNING [{route['origin']}-{route['destination']} {dep_date}]: {e}")
    return best


def search_multi_city(route: dict, config: dict) -> FlightOffer | None:
    client = _make_client()
    dates = _sample_dates(config["date_start"], config["date_end"], config["sample_dates"])
    date_end = date.fromisoformat(config["date_end"])
    segs = route["segments"]
    best: FlightOffer | None = None

    for dep_date in dates:
        for stay1 in [segs[0]["stay_min"], segs[0]["stay_max"]]:
            for stay2 in [segs[1]["stay_min"], segs[1]["stay_max"]]:
                mid_date = dep_date + timedelta(days=stay1)
                ret_date = mid_date + timedelta(days=stay2)
                if ret_date > date_end:
                    continue
                body = {
                    "currencyCode": "USD",
                    "originDestinations": [
                        {"id": "1",
                         "originLocationCode": segs[0]["origin"],
                         "destinationLocationCode": segs[0]["destination"],
                         "departureDateTimeRange": {"date": str(dep_date)}},
                        {"id": "2",
                         "originLocationCode": segs[1]["origin"],
                         "destinationLocationCode": segs[1]["destination"],
                         "departureDateTimeRange": {"date": str(mid_date)}},
                        {"id": "3",
                         "originLocationCode": segs[2]["origin"],
                         "destinationLocationCode": segs[2]["destination"],
                         "departureDateTimeRange": {"date": str(ret_date)}},
                    ],
                    "travelers": [{"id": "1", "travelerType": "ADULT"}],
                    "sources": ["GDS"],
                    "searchCriteria": {
                        "maxFlightOffers": 5,
                        "flightFilters": {
                            "connectionRestriction": {
                                "maxNumberOfConnections": route["max_stops"]
                            }
                        },
                    },
                }
                try:
                    resp = client.shopping.flight_offers_search.post(body)
                    offer = _extract_cheapest(resp.data, route["max_stops"])
                    if offer and (best is None or offer.price < best.price):
                        best = offer
                except ResponseError as e:
                    print(f"WARNING [multi-city {dep_date}/{stay1}/{stay2}]: {e}")
    return best
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_searcher.py -v
```

Expected: 11 tests PASSED.

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
pytest -v
```

Expected: All tests PASSED (analyzer + notifier + searcher).

- [ ] **Step 6: Commit**

```bash
git add code/searcher.py tests/test_searcher.py
git commit -m "feat: searcher — Amadeus flight search with mock tests"
```

---

## Task 5: main.py — Orchestration

**Files:**
- Create: `main.py`

- [ ] **Step 1: Write main.py**

```python
import yaml
from pathlib import Path

from code.analyzer import analyze
from code.notifier import FlightResult, append_to_csv, write_summary, send_desktop_notification
from code.searcher import search_round_trip, search_multi_city

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config" / "routes.yaml"
CSV_PATH = BASE_DIR / "data" / "prices.csv"
OUTPUT_PATH = BASE_DIR / "output" / "latest.txt"


def main() -> None:
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    search_cfg = cfg["search"]
    results: list[FlightResult] = []
    alerts = []

    for route in cfg["routes"]:
        print(f"Searching {route['name']}...")
        try:
            offer = (
                search_round_trip(route, search_cfg)
                if route["type"] == "round_trip"
                else search_multi_city(route, search_cfg)
            )
        except Exception as e:
            print(f"ERROR searching {route['name']}: {e}")
            continue

        if offer is None:
            print(f"  No results found for {route['name']}")
            continue

        result = FlightResult(
            route=route["name"],
            cheapest_price=offer.price,
            currency=offer.currency,
            departure_date=offer.departure_date,
            final_leg_date=offer.final_leg_date,
            stops=offer.stops,
            airline=offer.airline,
        )
        alert = analyze(CSV_PATH, route["name"], offer.price, search_cfg["alert_threshold"])
        append_to_csv(CSV_PATH, result)
        results.append(result)
        alerts.append(alert)

        if alert.should_alert:
            send_desktop_notification(result, alert)
            print(f"  ${offer.price:,.0f} — ALERT sent")
        else:
            print(f"  ${offer.price:,.0f} — logged (no alert)")

    if results:
        write_summary(OUTPUT_PATH, results, alerts)
        print(f"\nSummary written to {OUTPUT_PATH}")
    else:
        print("\nNo results to report.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify syntax**

```bash
cd /home/xao/projects/claude/flight-bot
source .venv/bin/activate
python -c "import main; print('main.py OK')"
```

Expected: `main.py OK`

- [ ] **Step 3: Run full test suite one more time**

```bash
pytest -v
```

Expected: All tests PASSED.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: main.py orchestration"
```

---

## Task 6: Credentials, Manual Test, and Systemd Service

- [ ] **Step 1: Register Amadeus free account**

Go to https://developers.amadeus.com → Sign up (free) → Create new app → copy `Client ID` and `Client Secret`.

- [ ] **Step 2: Create .env with credentials**

```bash
cp .env.example .env
# Edit .env — fill in real AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET
```

The `.env` file should look like:
```
AMADEUS_CLIENT_ID=AbCdEfGh1234567890
AMADEUS_CLIENT_SECRET=aBcDeFgH1234567890
```

- [ ] **Step 3: Run manually to verify end-to-end**

```bash
cd /home/xao/projects/claude/flight-bot
source .venv/bin/activate
python main.py
```

Expected output (approximate):
```
Searching Asia Grand Tour...
  $2,847 — ALERT sent
Searching Boston-HongKong RT...
  $1,205 — ALERT sent

Summary written to /home/xao/projects/claude/flight-bot/output/latest.txt
```

Check the CSV was created:
```bash
cat data/prices.csv
```

Expected: Header row + 2 data rows.

Check the summary:
```bash
cat output/latest.txt
```

Expected: Formatted price report for both routes.

- [ ] **Step 4: Create the systemd user service file**

```bash
mkdir -p ~/.config/systemd/user
```

Create `~/.config/systemd/user/flight-bot.service`:

```ini
[Unit]
Description=Flight Price Bot
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/home/xao/projects/claude/flight-bot
ExecStart=/home/xao/projects/claude/flight-bot/.venv/bin/python /home/xao/projects/claude/flight-bot/main.py
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

- [ ] **Step 5: Enable and test the service**

```bash
systemctl --user daemon-reload
systemctl --user enable flight-bot.service
systemctl --user start flight-bot.service
```

Check it ran successfully:
```bash
systemctl --user status flight-bot.service
```

Expected: `Active: inactive (dead)` with `Result: success` (it's a oneshot service — exits after running).

Check journal output:
```bash
journalctl --user -u flight-bot.service --no-pager
```

Expected: Same output as the manual run above.

- [ ] **Step 6: Write CLAUDE.md**

Create `CLAUDE.md` at project root:

```markdown
# flight-bot

Startup flight price tracker using Amadeus API.

## Setup

1. Register at https://developers.amadeus.com (free)
2. Copy `.env.example` to `.env` and fill in credentials
3. `test -d .venv || python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
4. `python main.py` to test manually

## Systemd service

Enable with: `systemctl --user enable flight-bot.service`
Runs automatically on login after network is up.
Logs: `journalctl --user -u flight-bot.service`

## Routes

Configured in `config/routes.yaml`. Date window and alert threshold are in the `search:` section.

## Output

- `data/prices.csv` — full price history
- `output/latest.txt` — last run summary
```

- [ ] **Step 7: Final commit**

```bash
git add main.py CLAUDE.md
git commit -m "feat: manual test verified, systemd service installed, CLAUDE.md added"
```

---

## API Call Budget

| Route | Calls per run |
|-------|--------------|
| Asia Grand Tour | 8 dates × 2 stay1 × 2 stay2 = 32 |
| Boston–HKG RT | 8 dates × 2 stays = 16 |
| **Total** | **48 calls/run** |

Amadeus free tier: 2,000 calls/month → supports ~40 startups/month comfortably.
