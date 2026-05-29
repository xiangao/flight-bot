# flight-bot

Daily flight price monitor that tracks two routes, publishes a live HTML dashboard to GitHub Pages, and sends desktop notifications when prices are notable.

**Live site:** https://xiangao.github.io/flight-bot-site/

## Routes

| Route | Provider | Type | Notes |
|-------|----------|------|-------|
| Asia Grand Tour | SerpAPI | Multi-city | BOS→NRT, then NRT→KIX overland, KIX→HKG, HKG→BOS. True multi-city fare — SerpAPI/Google Flights required. |
| BOS ↔ Beijing / Shanghai / HK | Ignav | Round-trip | Searches all 3 destinations, picks cheapest. Nonstop only. |

Both routes are configured in `config/routes.yaml`.

## Setup

### 1. API keys

- **Ignav** — register at https://ignav.com/ (round-trip searches)
- **SerpAPI** — register at https://serpapi.com/ (multi-city searches; 100 free searches/month)

Copy `.env.example` to `.env` and fill in:

```
IGNAV_API_KEY=...
SERPAPI_KEY=...
```

### 2. Install dependencies

```bash
test -d .venv || python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. GitHub Pages site

```bash
mkdir site && cd site
git init
git checkout -b gh-pages
git remote add origin https://github.com/xiangao/flight-bot-site.git
cd ..
```

### 4. Run manually

```bash
source .venv/bin/activate
python main.py
```

## Scheduling (systemd timer)

```bash
cp flight-bot.service flight-bot.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now flight-bot.timer
```

Logs: `journalctl --user -u flight-bot.service`

## Configuration

All search parameters are in `config/routes.yaml`:

```yaml
search:
  date_start: "2026-10-01"   # search window start
  date_end:   "2026-11-30"   # search window end
  sample_dates: 4            # number of departure dates to sample
  alert_threshold: 0.10      # alert if price is 10%+ below recent average
  cache_hours: 6             # cache API responses for this many hours
  cabin_class: economy
```

Each route can override `provider`, `max_stops`, and `min_stops`:

- `max_stops: 0` — show only nonstop panel; `max_stops: 1` — show both nonstop and 1-stop panels
- `min_stops: 1` — hide the nonstop panel (e.g. if nonstop is impractical for a route)

**Provider notes:**
- `ignav` — supports round-trip directly; does NOT support multi-city (would sum 3 separate one-ways, giving unrealistic prices)
- `serpapi` — required for multi-city routes; 100 free searches/month quota

## Output

| File | Contents |
|------|----------|
| `data/prices.csv` | Round-trip price history (Ignav routes) |
| `data/round_trip_prices.csv` | Same, legacy name |
| `output/latest.txt` | Last run summary |
| `output/listings.html` | HTML dashboard (copied to `site/index.html` on deploy) |

## Dashboard

Each route card shows:
- **Nonstop** and **1-stop** panels side by side (controlled by `min_stops`/`max_stops`)
- Per-segment details: airports, local times, flight number, aircraft type, layover durations
- Price history table with nonstop and 1-stop columns for easy comparison
- "Last seen" fallback when live search is unavailable (e.g. SerpAPI quota exhausted)
