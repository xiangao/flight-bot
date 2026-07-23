# flight-bot: per-date-pair tracking + trip detail (2026-07-23)

## Motivation

`search_multi_city_scrape`/`search_round_trip_scrape` (and, in fact, all
three providers' search functions — SerpAPI, Ignav, scrape) already loop over
every sampled date combination for a route (~68 combos/run for Asia Grand
Tour: ~17 Tue/Thu outbound dates × 2 BOS-NRT stay options × 2 KIX-SHA stay
options; ~57/run for Beijing: ~57 daily outbound dates × 1 fixed 21-day stay)
— but only the single overall-cheapest result per stop-count survives; every
other combination's result is discarded. The dashboard therefore can only
show "the cheapest fare found today," never "what does *this specific* date
combination cost, and how has it moved." Since the search cost is already
paid searching every combination, persisting each one is close to free —
this is a data-retention and dashboard change, not a new search-cost problem.

Separately, the existing "Price History" table's Outbound/Inbound/Total
columns are built around SerpAPI/Ignav's two-directional structured segment
data (`_parse_legs` expects `Outbound:`/`Inbound:` headers) and show blank
`—` for every scrape-sourced row, since scrape's `details` text uses a
different, already-richer "Leg 1/2/3" format (added in the prior session's
detail-enrichment fix). Rather than leaving those columns blank, show the
leg detail that's actually available.

## Scope

- Scrape provider only (`search_multi_city_scrape`/`search_round_trip_scrape`).
  SerpAPI/Ignav's search functions are NOT changed to persist per-combo data —
  they remain fallback-only and don't need this enhancement; they gain a
  trivial return-shape change (see Architecture) so `main.py`'s calling code
  stays uniform regardless of provider.
- Both routes: Asia Grand Tour (multi-city, 3 legs → 3 date columns) and BOS
  ↔ Beijing (round-trip, 2 legs → 2 date columns).
- The existing "cheapest-overall + rolling-average alert" summary (top
  banner per route card, backed by `data/prices.csv`/`data/round_trip_prices.csv`
  and `code/analyzer.py`) is **unchanged** — it keeps working exactly as
  today, feeding the same alert logic. Nothing about it is removed or altered.
- The existing "Price History" table (one row per calendar day, deduped,
  showing the day's cheapest fare) is **kept**, with its blank
  Outbound/Inbound/Total columns replaced by a single "Details" column of
  actual leg text — same fix applied to both this table and the new one below.
- **New**: a second table per stop-count, "All Sampled Dates" — one row per
  distinct date combination, showing that combination's *latest* observed
  price (not all-time-cheapest), sorted cheapest-first, with full leg detail
  inline. This is what answers "check multiple specific trips across
  Oct–Dec," not just "what's the single best one today."

## Architecture

**`code/searcher.py`**: `search_round_trip_scrape`/`search_multi_city_scrape`
change their return type from `dict` to `tuple[dict, list[dict]]`:

```python
def search_multi_city_scrape(route: dict, config: dict) -> tuple[dict, list[dict]]:
    """Returns (best, all_results).
    best: {0: FlightOffer|None, 1: FlightOffer|None} -- unchanged, feeds the
      existing alert/notifier flow exactly as before.
    all_results: one entry per (date-combo, stop-count) that returned a fare
      -- {"dates": [dep_date_str, mid_date_str, ret_date_str], "stops": 0|1,
      "offer": FlightOffer}. Empty list if nothing was found. Feeds the new
      per-date-pair CSV/table only.
    """
```

Round-trip's `all_results` entries have `"dates": [dep_date_str, ret_date_str]`
(2 elements) instead of 3. Everything else about these two functions' search
loops is unchanged — this is purely capturing what's already being computed
and currently thrown away, not searching anything new or differently.

`search_round_trip_serpapi`/`search_round_trip_ignav`/
`search_multi_city_serpapi`/`search_multi_city_ignav` (fallback providers,
out of scope for real per-combo tracking) get a one-line change to their
`return best` statements → `return best, []`, so the dispatch functions
(`search_round_trip`/`search_multi_city`) can return a uniform
`(best, all_results)` tuple regardless of which provider actually ran,
without touching those functions' search logic at all.

**`main.py`**: unpacks the new tuple —

```python
offers_by_stops, pair_results = search_fn(route, route_search_cfg)
```

The existing per-stop-count loop (building `FlightResult`, calling `analyze`,
appending to `csv_path`, sending notifications) is **unchanged**, just reads
from `offers_by_stops` as it already did. A new step appends `pair_results`
(if non-empty) to a new per-route-type CSV, using a new small writer function
in `code/notifier.py`.

## Data model

Two new CSV files (route/type-column convention matches the existing
`prices.csv`/`round_trip_prices.csv` split by type):

- **`data/prices_by_pair.csv`** (multi-city / Asia Grand Tour):
  `ts, route, dep_date, mid_date, ret_date, stops, price, currency, airline, details`
- **`data/round_trip_prices_by_pair.csv`** (round-trip / Beijing):
  `ts, route, dep_date, ret_date, stops, price, currency, airline, details`

Both are gitignored the same way the existing data files are (already
covered by this repo's blanket `data/` `.gitignore` line — no change needed
there). No archiving needed for these — they're brand new, empty at first
run, nothing pre-existing to reconcile.

## Dashboard (`code/html_writer.py`)

- **`_parse_legs`/`_airport_codes_from_details`**: extend to also recognize
  the "Leg 1: ORIGIN→DEST (date) — ..." format (not just the old
  `Outbound:`/`Inbound:` header format), so both the existing history table
  and the new table can extract airport codes for the synthesized "Search ↗"
  link, and so a "Details" column can render the leg lines directly
  (no parsing needed for that part — the stored `details` text is already
  human-readable).
- **Existing "Price History" table**: replace the `Outbound`/`Inbound`/`Total`
  three columns with one `Details` column showing the row's stored `details`
  text verbatim (already multi-line, already readable). Column header row and
  cap-at-30-rows behavior unchanged.
- **New "All Sampled Dates" table**, one per stop-count, added after the
  existing history table: loads the relevant `*_by_pair.csv`, keeps the
  *latest* observation per unique date-combo (same "latest, not all-time"
  rule jal-bot's dashboard uses), sorted cheapest-first. Columns: `Outbound`
  (dep_date), `Mid` (multi-city only — omitted entirely for round-trip's
  2-date shape, not shown as a blank column), `Return` (ret_date),
  `Latest price`, `Details`, `As of`, `Book ↗` (same synthesized Google
  Flights search link the existing table already builds, reused as-is).
- Table is omitted entirely (not rendered as an empty shell) when the
  `*_by_pair.csv` doesn't exist yet or has no rows for that stop-count —
  same convention `_history_table` already follows.

## Testing

- `code/searcher.py`: update the three existing scrape-provider tests
  (`test_search_round_trip_scrape_returns_nonstop_and_onestop`,
  `test_search_multi_city_scrape_returns_nonstop_and_onestop`,
  `test_search_round_trip_scrape_excludes_turkish_airlines`) for the new
  `(best, all_results)` return shape; add one new test per function
  asserting `all_results` actually contains one entry per sampled
  date-combo × stop-count (not just the winner).
- `code/notifier.py`: new unit test for the new pair-row CSV writer
  (round-trip 2-date shape and multi-city 3-date shape both covered).
- `code/html_writer.py`: new unit tests for the "Leg 1:" format recognition
  in `_parse_legs`/`_airport_codes_from_details`, and for the new "All
  Sampled Dates" table's latest-per-combo + sort-cheapest-first logic
  (mirroring jal-bot's `test_html_writer.py:_panel_data` tests, adapted to
  this project's data shape).

## Explicitly out of scope

- Persisting per-combo data for SerpAPI/Ignav (fallback-only, not worth the
  added complexity for providers that aren't the default).
- Any change to the existing rolling-average alert logic, its CSV files, or
  its notification behavior.
- Retroactively backfilling `*_by_pair.csv` history — it starts empty and
  accumulates from the next real run onward, same "cold start" as any newly
  added tracked series.
