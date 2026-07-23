# flight-bot

Daily flight price tracker. Ignav for round-trips, SerpAPI for multi-city.

## Architecture update (2026-07-23 — scrape is now the default provider)

Both routes now default to `provider: scrape` — jal-bot's proven, free
Google-Flights-scraping technique (headful Akamai-defeating Chrome + a
from-scratch deep-link protobuf encoder + aria-label parser), vendored into
`code/browser.py`/`code/gflights.py`/`code/gflights_searcher.py`. SerpAPI and
Ignav remain available as an explicit per-route fallback (`provider: serpapi`
or `provider: ignav`) — not removed, just no longer the default. See
`docs/superpowers/specs/2026-07-23-scraping-rewrite-design.md` for the full
design and `docs/superpowers/plans/2026-07-23-scraping-rewrite.md` for how it
was implemented.

- **Why:** flight-bot used a paid, quota-limited API since its first commit
  (2026-05-03) because no scraping alternative existed anywhere in this
  workspace at the time. jal-bot built one later (for a different problem —
  JAL's own Akamai-gated site) and it turned out to also work directly
  against Google Flights, for free, with no bot-gate hit in practice.
- **What's lost:** scraping only exposes total price + the first leg's detail
  (airline/stops/duration/one layover) per fare card, not full per-direction
  segment detail the way SerpAPI/Ignav gave it. Scrape-sourced CSV rows have a
  simpler one-line `details` field; the dashboard's live-panel segment
  timeline falls back to showing just the airline name for these rows
  (`code/html_writer.py:_render_stop_panel`'s existing no-structured-segments
  fallback — unchanged code, already handled this case).
- **Route shape changed too**: Asia Grand Tour's middle segment moved from
  KIX→HKG to KIX→SHA (Shanghai), with SHA→HKG self-arranged/untracked (same
  convention as the pre-existing untracked NRT→KIX gap).
- **Timer moved from every-6-days to daily** (`OnCalendar=*-*-* 09:30:00`) —
  the 6-day cadence was SerpAPI-quota-driven (250 searches/month); no longer
  applicable once scraping is the default.
- **Old data archived, not migrated**: `data/prices.csv`/`round_trip_prices.csv`
  (SerpAPI/Ignav-era, old Sep-Oct date window) moved to `data/archive/` — both
  the CSV `details` shape and the date window changed at once, so reconciling
  old and new rows in one file wasn't worth it.

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
- **Ignav does carry Hainan fares** (verified 2026-07-22 by querying its API directly for
  BOS-PEK: returned Hainan flight HU730/729 as one correctly-nonstop segment, priced
  identically to SerpAPI). A dedicated `provider: serpapi` route existed for Hainan's
  BOS-PEK route on the belief that Ignav didn't carry it — that belief was wrong, and the
  route was retired as redundant. Don't recreate a single-airline SerpAPI route based on
  an assumption that Ignav lacks a carrier's fares without checking the API directly first.

## Output

- `data/prices.csv` — multi-city price history
- `data/round_trip_prices.csv` — round-trip price history
- `output/latest.txt` — last run summary
- `site/` — separate git repo, gh-pages branch → https://xiangao.github.io/flight-bot-site/

### Dashboard price-history tables

`code/html_writer.py:_history_table(rows, route_cfg, stops)` renders **one section
per stop count** — `_render_card` calls it for 0 and 1, so each route card shows a
separate "Nonstop — Price History" and "1 Stop — Price History" table (a section is
omitted when that stop count has no rows). Columns: Date · Price · Airline · Travel
dates · Outbound · Inbound · Total · link. All from the CSV — no extra API calls.

- **The `stops` split is on the CSV `stops` column = the *outbound* stop count.**
  Both leg summaries are shown because the inbound can differ (e.g. a nonstop
  outbound with a 1-stop inbound).
- **Leg durations + connection airports are parsed from the `details` text**
  (`_parse_legs`): each `Outbound:/Inbound:` header gives the leg duration and stop
  count; segment lines give the via-airports. Total = outbound + inbound.
- **The "Search ↗" link is synthesized, not stored.** Neither Ignav nor SerpAPI
  returns a bookable deep link (Ignav gives only an internal `ignav_id`), so
  `_gflights_link` builds a Google Flights search URL from the row's
  origin/destination/dates. This stays valid for every historical row.
- **Airport codes / durations come from the CSV `details` column.** The multi-city
  `prices.csv` has no `details`, so those rows show `—` for legs/total and no link
  (route-config origin only).
- History is sorted by the real ISO timestamp, not the `"%b %d"` label (the label
  isn't chronological across months).
