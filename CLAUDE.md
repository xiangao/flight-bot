# flight-bot

Daily flight price tracker. Ignav for round-trips, SerpAPI for multi-city.

## Setup

1. Register at https://ignav.com/ and https://serpapi.com/ for API keys
2. Copy `.env.example` to `.env` and fill in `IGNAV_API_KEY` and `SERPAPI_KEY`
3. `test -d .venv || python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
4. `python main.py` to test manually

## Systemd timer

```bash
cp flight-bot.service flight-bot.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now flight-bot.timer
```

Logs: `journalctl --user -u flight-bot.service`

## Routes

Configured in `config/routes.yaml`. Date window and alert threshold are in the `search:` section.

- Use `provider: ignav` for round-trips — supports nonstop + 1-stop in one API call
- Use `provider: serpapi` for multi-city — required for true multi-city fares; summing one-ways via Ignav gives unrealistic prices
- `max_stops` / `min_stops` on a route controls which stop-count panels are shown in the dashboard

## Output

- `data/prices.csv` — multi-city price history
- `data/round_trip_prices.csv` — round-trip price history
- `output/latest.txt` — last run summary
- `site/` — separate git repo, gh-pages branch → https://xiangao.github.io/flight-bot-site/

### Dashboard price-history table

`code/html_writer.py:_history_table` renders a flat detail row per (date, stop-count):
Date · Stops · Price · Airline · Travel dates · link. All fields come straight
from the CSV — no extra API calls.

- **The "Search ↗" link is synthesized, not stored.** Neither Ignav nor SerpAPI
  returns a bookable deep link (Ignav gives only an internal `ignav_id`), so
  `_gflights_link` builds a Google Flights search URL from the row's
  origin/destination/dates. This stays valid for every historical row.
- **Airport codes are parsed from the CSV `details` column** (`\b[A-Z]{3}\b` on
  the outbound block) — ground truth for that row. The multi-city `prices.csv`
  has no `details` column, so those rows get no link (route-config origin only).
- History is sorted by the real ISO timestamp, not the `"%b %d"` label (the label
  isn't chronological across months).
