# flight-bot

Daily flight price tracker over Google Flights (scraped directly, free), with Ignav/SerpAPI available as explicit fallback providers.

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
  multi-line `details` field listing every leg's route+date, with full
  stops/airline/duration/layover only for leg 1; the dashboard's live-panel
  fallback (`code/html_writer.py:_render_stop_panel`, for rows with no
  structured segments) renders that `details` text directly rather than just
  the bare airline name (fixed 2026-07-23, same day as the per-date-pair
  tracking work below).
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

## Per-date-pair tracking (2026-07-23)

Both routes already search many date combinations per run (`search_multi_city_scrape`/
`search_round_trip_scrape` loop over every sampled outbound date × stay-length
option — ~127+ combos/run for Asia Grand Tour, ~57+ for Beijing), but historically
only the single overall-cheapest result per stop-count survived; every other
combo searched was discarded. Since the search cost is already paid, every
combo's result is now also captured and persisted:

- `search_round_trip_scrape`/`search_multi_city_scrape` return
  `(best, all_results)` instead of a bare `best` dict — `all_results` has one
  entry per (date-combo, stop-count) that returned a fare. The four
  fallback-provider search functions (SerpAPI/Ignav) only got a trivial
  `return best, []` change — they don't persist per-combo data (out of scope,
  fallback-only) but the dispatchers can now treat all providers uniformly.
- `code/notifier.py:append_pair_rows` writes those to
  `data/prices_by_pair.csv`/`data/round_trip_prices_by_pair.csv` — see Output
  section below for schema.
- `code/html_writer.py`'s new "All Sampled Dates" table (see Dashboard section
  below) is the payoff: check a specific trip's own price trend across
  Oct-Dec, not just "what's the single cheapest thing found today."
- The existing cheapest-overall + rolling-average alert system
  (`data/prices.csv`/`round_trip_prices.csv`, `code/analyzer.py`, the top
  summary panel) is completely unchanged by this — same files, same columns,
  same alert logic.
- See `docs/superpowers/specs/2026-07-23-per-date-pair-tracking-design.md` and
  `docs/superpowers/plans/2026-07-23-per-date-pair-tracking.md` for the full
  design/implementation.

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

- Default `provider: scrape` (Google Flights) — free, no quota, no API key needed
- `provider: ignav` available as explicit fallback for round-trips — supports nonstop + 1-stop in one API call
- `provider: serpapi` available as explicit fallback for multi-city — required for true multi-city fares; summing one-ways via Ignav gives unrealistic prices
- `max_stops` / `min_stops` on a route controls which stop-count panels are shown in the dashboard
- **Ignav does carry Hainan fares** (verified 2026-07-22 by querying its API directly for
  BOS-PEK: returned Hainan flight HU730/729 as one correctly-nonstop segment, priced
  identically to SerpAPI). A dedicated `provider: serpapi` route existed for Hainan's
  BOS-PEK route on the belief that Ignav didn't carry it — that belief was wrong, and the
  route was retired as redundant. Don't recreate a single-airline SerpAPI route based on
  an assumption that Ignav lacks a carrier's fares without checking the API directly first.

## Output

- `data/prices.csv` — multi-city cheapest-overall-per-run price history (feeds the
  rolling-average alert)
- `data/round_trip_prices.csv` — round-trip cheapest-overall-per-run price history
  (same alert role)
- `data/prices_by_pair.csv` — multi-city, one row per sampled (dep, mid, ret) date
  combo per run (2026-07-23 addition — every combo searched, not just the winner)
- `data/round_trip_prices_by_pair.csv` — round-trip equivalent, one row per (dep, ret)
- `output/latest.txt` — last run summary
- `site/` — separate git repo, gh-pages branch → https://xiangao.github.io/flight-bot-site/

### Dashboard price-history tables

Each route card has two tables per stop count (0/1), stacked:

1. **"Price History"** (`code/html_writer.py:_history_table`) — one row per
   calendar day, deduped, showing that day's cheapest-overall fare. Columns:
   Date · Price · Airline · Travel dates · **Details** · link. The old
   Outbound/Inbound/Total columns (and the `_parse_legs`/`_leg_summary` helpers
   that built them) were removed 2026-07-23 — scrape-sourced rows never had
   the structured two-directional data those columns needed, so they always
   showed blank `—`. The Details column instead renders the row's stored
   `details` text verbatim (already human-readable, multi-line).
2. **"All Sampled Dates"** (`code/html_writer.py:_pair_table`, added
   2026-07-23) — one row per distinct date combination actually searched (not
   just the single cheapest), sourced from `data/prices_by_pair.csv`/
   `data/round_trip_prices_by_pair.csv`. Shows the *latest* observation per
   combo (`_latest_per_pair` — same "latest, not all-time" rule the summary
   panel uses), sorted cheapest-first. Multi-city routes get an extra `Mid`
   column (the middle leg's date); round-trip routes don't.

Both tables:
- **The `stops` split is on the CSV `stops` column = the *first leg's* stop count.**
- **The "Search ↗" link is synthesized, not stored.** Neither Ignav nor SerpAPI
  returns a bookable deep link (Ignav gives only an internal `ignav_id`), so
  `_gflights_link` builds a Google Flights search URL from the row's
  origin/destination/dates.
- **Airport codes come from the CSV `details` column**, via
  `_airport_codes_from_details`, which as of 2026-07-23 branches on format: the
  legacy SerpAPI/Ignav `Outbound:`/`Inbound:` text still uses a first/last-code
  scan (correctly surviving a connecting outbound flight); the newer scrape
  `Leg 1: X→Y (date) — ...` format matches the leg-1 arrow pair directly
  (`Leg 1:\s*([A-Z]{3})...([A-Z]{3})`) rather than scanning the whole leg-1
  block for bare 3-letter tokens — a naive whole-block scan collides with
  3-letter airline codes appearing in the descriptive text (e.g. "JAL"),
  picking the wrong "destination". **Known pre-existing gap**: this fix
  doesn't cover `search_multi_city_ignav`'s combined-legs `details` format
  (no `Outbound:`/`Inbound:` *or* `Leg 1: X→Y` shape), which would still
  mis-extract if `provider: ignav` were ever set on a multi-city route — not
  reachable today since both routes default to `provider: scrape`.
- History is sorted by the real ISO timestamp, not a display label (labels
  aren't chronological across months).
