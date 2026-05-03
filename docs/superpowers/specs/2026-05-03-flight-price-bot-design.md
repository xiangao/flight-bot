# Flight Price Bot — Design Spec
**Date:** 2026-05-03  
**Status:** Approved

---

## Overview

A Python script that runs once at machine startup, searches Amadeus for the cheapest prices on two configured routes, logs results to a CSV, and fires a desktop notification when the price drops notably below the recent average.

---

## Routes

### Route 1 — Asia Grand Tour (Multi-city)
| Leg | Origin | Destination | Stay |
|-----|--------|-------------|------|
| 1 | BOS | TYO (NRT or HND) | 7–10 days |
| 2 | OSA (KIX) | HKG | 14–18 days |
| 3 | HKG | BOS | — |

- Type: Multi-city (3 segments)
- Max stops per segment: 1
- Tokyo → Osaka internal travel is out of scope (shinkansen, not a flight)
- Stay flexibility: searcher samples all combinations of stay durations within each range and keeps the cheapest overall itinerary

### Route 2 — Boston–Hong Kong Round Trip
- Origin: BOS
- Destination: HKG
- Stay: 18–25 days
- Type: Round trip
- Max stops: 1

### Date Window
- Start: 2026-09-01
- End: 2026-11-30
- Strategy: Each run samples 8 evenly-spaced departure dates across the window; keeps the single cheapest result per route per run.

---

## Project Structure

```
~/projects/claude/flight-bot/
├── config/
│   └── routes.yaml          # routes, date window, alert threshold
├── data/
│   └── prices.csv           # persistent price history
├── code/
│   ├── searcher.py          # Amadeus API calls
│   ├── analyzer.py          # rolling average + alert logic
│   └── notifier.py          # desktop notification + CSV write
├── output/
│   └── latest.txt           # human-readable summary of last run
├── main.py                  # entry point
├── .env                     # Amadeus API credentials (gitignored)
├── .gitignore
├── requirements.txt
└── CLAUDE.md
```

---

## Components

### `config/routes.yaml`
Declares both routes, the search date window, number of sample dates per run, and the alert threshold.

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

### `code/searcher.py`
- Authenticates with Amadeus using OAuth2 client credentials (token expires in 30 min — fresh token per run)
- For round-trip routes: calls `GET /v2/shopping/flight-offers` with `originLocationCode`, `destinationLocationCode`, `departureDate`, `returnDate`, `max=1`, `nonStop=false`, `maxNumberOfConnections=1`
- For multi-city routes: calls `POST /v2/shopping/flight-offers` with `originDestinations` array (3 legs), iterating over combinations of departure date × stay durations within `stay_min`/`stay_max` ranges
- Returns the single cheapest itinerary across all sampled departure dates and stay-length combinations

### `code/analyzer.py`
- Reads `data/prices.csv`
- Computes 7-day rolling average price per route
- Returns `(should_alert: bool, pct_below: float, avg_price: float)` per route
- Logic:
  - Fewer than 7 historical rows for route → `should_alert = True` (show prices while history builds)
  - Current price ≥ 10% below rolling average → `should_alert = True`
  - Otherwise → `should_alert = False`

### `code/notifier.py`
- Appends one row to `data/prices.csv` per route per run
- Writes human-readable summary to `output/latest.txt`
- If `should_alert`: fires `notify-send` desktop notification with urgency `normal`

**CSV schema:**
```
timestamp,route,cheapest_price,currency,departure_date,final_leg_date,stops,airline
```
- `departure_date`: first outbound leg departure date
- `final_leg_date`: departure date of the last leg (for round-trip: return flight; for multi-city: final leg e.g. HKG→BOS)

**Notification format:**
```
✈ Price Alert: Asia Grand Tour
$2,847 — 14% below recent avg ($3,310)
Best dates: Sep 15 → Oct 6  |  JAL, 1 stop
```

### `main.py`
Orchestrates: load config → authenticate → search all routes → analyze → notify → exit.

---

## Startup Trigger

A `systemd` user service that runs `main.py` after the network comes up on login.

**File:** `~/.config/systemd/user/flight-bot.service`

```ini
[Unit]
Description=Flight Price Bot
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/home/xao/projects/claude/flight-bot
ExecStart=/home/xao/projects/claude/flight-bot/.venv/bin/python main.py
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

Enabled with: `systemctl --user enable flight-bot.service`

---

## Data Flow

```
startup
  └─ main.py
       ├─ load config/routes.yaml
       ├─ searcher.py: authenticate → search Route 1 (8 dates × 3 legs)
       ├─ searcher.py: authenticate → search Route 2 (8 dates)
       ├─ analyzer.py: load prices.csv → compute rolling avg → alert decision
       └─ notifier.py:
            ├─ append rows to prices.csv
            ├─ write output/latest.txt
            └─ [if alert] notify-send desktop popup
```

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| No internet at startup | systemd `After=network-online.target` delays execution; if still fails, logged to journal |
| Amadeus API rate limit / error | Log error to `output/latest.txt`, skip that route, do not crash |
| No flights found for a date | Skip that sample date, continue with others |
| CSV missing / first run | Create file with header, proceed normally |
| `notify-send` unavailable | Log warning, continue — CSV still updated |

---

## Dependencies

```
amadeus>=9.0.0
python-dotenv>=1.0.0
pyyaml>=6.0
requests>=2.31.0
```

---

## Setup Steps (for implementation)

1. `pip install amadeus` and other deps into `.venv`
2. Register free account at developers.amadeus.com → get `AMADEUS_CLIENT_ID` and `AMADEUS_CLIENT_SECRET`
3. Add credentials to `.env`
4. Install and enable systemd user service
5. Test with `python main.py` manually before enabling service

---

## Out of Scope

- Booking / purchasing tickets
- Email notifications (can be added later)
- Price history visualization (data is in CSV — easy to add later)
- Mobile notifications
