# flight-bot: scraping rewrite (2026-07-23)

## Motivation

flight-bot has used a paid, quota-limited API (Amadeus, then SerpAPI for
multi-city / Ignav for round-trip) since its first commit (2026-05-03), because
at the time no scraping alternative existed anywhere in this workspace. jal-bot
later built one (2026-06-01 through 2026-07-22): a headful, Akamai-defeating
Chrome launcher (originally for rental-car-bot's Costco problem) plus a
from-scratch Google Flights deep-link protobuf encoder and aria-label parser
(built for JAL's own Akamai-gated site, then found to also work directly
against Google Flights, with no bot-gate hit in practice). That technique is
free (no API quota) and already proven live across both jal-bot's own route
and — via the live probe below — a real 3-leg multi-city shape matching
flight-bot's "Asia Grand Tour" route.

This spec ports that technique into flight-bot as the new default search
provider, keeping SerpAPI/Ignav as an explicit fallback rather than removing
them.

## Scope

- Both flight-bot routes: "Asia Grand Tour" (multi-city) and "BOS ↔ Beijing
  (PEK)" (round-trip).
- Route shape for Asia Grand Tour changes as part of this work: the middle
  segment moves from KIX→HKG to **KIX→SHA** (Shanghai), with **SHA→HKG**
  self-arranged/untracked (mirroring today's untracked NRT→KIX gap), then
  **HKG→BOS** home. Stay windows unchanged: BOS→NRT stay 7–10 days; the
  Shanghai-arrival-to-Hong-Kong-departure window stays 14–18 days (now
  covering Shanghai time + the self-arranged SHA→HKG leg + Hong Kong time
  combined, not just Hong Kong time as before).
- Beijing route's date window moves to match Asia Grand Tour's: outbound
  2026-10-06 → 2026-12-01 (see jal-bot's own 2026-07-23 date-window change for
  precedent on how `date_end` must be computed as last-outbound-date +
  max-total-stay, not copied literally — same trap applies here).

## Architecture

Vendor jal-bot's scraping stack into flight-bot as new files, unchanged in
logic (same "vendor, don't import" convention jal-bot itself used pulling
`browser.py` from rental-car-bot):

- `code/browser.py` — headful Chrome over CDP, Xvfb-backed, real-GPU WebGL
  (vulkan) to defeat Akamai-style bot detection. flight-bot has no browser
  automation today; this is wholly new to it. Live testing (see Verification)
  suggests Google Flights itself may not even require the heavy anti-detection
  measures this was built for (JAL's and Costco's actually-gated sites) — kept
  anyway since it's proven and safe to keep even if unnecessary.
- `code/gflights.py` — the `gflights_url(legs, seat, adults)` deep-link
  protobuf builder, unchanged from jal-bot. A round-trip is just a 2-leg
  multi-city search (A→B, then B→A) — the same function handles both flight-bot
  route types, so there is one new search code path, not two.
- `code/gflights_searcher.py` — `search_all_options`, `pick_cheapest_nonstop`,
  `pick_cheapest_alternative`, unchanged from jal-bot.

`code/searcher.py` keeps its existing `_provider(config)` dispatch
(`serpapi`/`ignav` remain available, untouched, as an explicit per-route
fallback). A new `provider: scrape` value is added, becoming the default for
both routes' configs. New functions `search_round_trip_scrape(route, config)`
and `search_multi_city_scrape(route, config)` call the vendored scraper and
adapt its output into flight-bot's existing `FlightOffer`-shaped return value,
so `analyzer.py`/`notifier.py` keep working unchanged.

## Data model & dashboard changes

- **`details` field simplifies for scrape-sourced rows.** SerpAPI/Ignav gave
  structured per-leg data for every direction, which
  `html_writer._parse_legs`/`_history_table` renders as separate
  Outbound:/Inbound: sections (stops, duration, connection airports) per
  direction. Scraping only exposes the **first leg** of each fare card's
  summary (total price + one leg's airline/stops/duration/one layover) — the
  same limitation jal-bot already lives with. Scrape-sourced rows get a
  simpler, one-line `details` (first-leg only); pre-existing SerpAPI/Ignav rows
  keep their richer detail as-is. `html_writer` must handle both shapes without
  erroring on the sparser one — this is an intentional, accepted trade-off, not
  a bug to fix later.
- **Nonstop and cheapest-connecting tracked as two parallel series per route**,
  matching jal-bot's M4 pattern, rather than relying on one `stops`-column
  split in a single CSV. Keeps the existing "nonstop panel / 1-stop panel"
  dashboard concept per route.
- **CSV column shape changes.** Asia Grand Tour needs three date columns
  (BOS→NRT date, KIX→SHA date, HKG→BOS date) instead of a simple out/return
  pair; Beijing's round-trip CSV shape is unaffected (still out/return).
- **Old data archived, not migrated.** Both the date window and the CSV schema
  are changing at once, so `data/prices.csv` and `data/round_trip_prices.csv`
  move to `data/archive/` (same pattern as jal-bot's 2026-07-23 date-window
  change) rather than trying to reconcile old SerpAPI/Ignav-shaped Sep–Oct rows
  with new scrape-shaped Oct–Dec rows in one file.

## Verification done during design

Live-probed the exact 3-leg shape (BOS→NRT, KIX→HKG placeholder legs — same
leg count/structure as the now-confirmed BOS→NRT/KIX→SHA/HKG→BOS shape) through
jal-bot's actual `gflights_url` + `search_all_options` code, unmodified: 36 raw
aria-label fragments, 7 correctly parsed into full options (price, airline,
stops, first-leg times/duration/layover). Confirms the core technique
generalizes to a real 3-leg multi-city trip, not just jal-bot's 2-leg case —
this was the main open technical risk before committing to this design.

## Rollout

- `flight-bot.timer` moves from every-6-days to daily
  (`OnCalendar=*-*-* 09:30:00`), matching jal-bot — no longer quota-constrained
  once `scrape` is the default provider.
- New unit tests for `search_round_trip_scrape`/`search_multi_city_scrape`
  (fixture aria-labels, same style as jal-bot's `test_gflights_searcher.py` and
  flight-bot's own `test_searcher.py`), covering both the round-trip-as-2-leg
  case and the 3-leg Asia Grand Tour case.
- Both routes' `config/routes.yaml` `provider:` field changes to `scrape`.
  `SERPAPI_KEY`/`IGNAV_API_KEY` stay in `.env.example` — still usable as an
  explicit per-route fallback, just not exercised by default.
- `CLAUDE.md`/`README.md` updated: new default provider, simplified
  scrape-sourced `details`, archived old data, new route shape, new timer
  cadence.
- Live-verify before calling this done: a real run against both routes,
  confirming prices land in the new CSVs/panels and the dashboard renders
  correctly — same bar jal-bot's milestones were held to.

## Explicitly out of scope

- Removing SerpAPI/Ignav code, tests, or dependencies — kept as fallback.
- Recovering full both-directions itinerary detail for scraped rows (would
  require clicking into each fare's expanded itinerary panel — extra
  scraping surface, unproven, and rejected during design discussion in favor
  of matching jal-bot's simpler total-price + first-leg-detail model).
