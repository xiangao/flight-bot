"""Open-jaw / multi-city outbound fare options, read live off Google Flights.

Google Flights has no true multi-city price calendar, so each itinerary is one
page load: build the `tfs` deep-link (code/gflights.py), open it on the
anti-bot browser, and read every fare card's summary `aria-label` — each one is
"From $X total. <Nonstop|N stop> flight with <airline>. Leaves <airport> at
<time> on <date> and arrives at <airport> at <time> on <date>. Total duration
<Xhr Ymin>. [Layover ... at <airport> in <city>.]" — where the stop-count /
airline / times / duration / layover describe the FIRST leg of the search
only, and the price is the whole trip's total. `search_all_options` returns
every option parsed this way, cheapest first, at zero extra scraping cost (one
page load already lists them all). `pick_cheapest_nonstop` isolates the
cheapest nonstop-first-leg option; `pick_cheapest_alternative` surfaces the
cheapest connecting (non-nonstop) option for comparison.

Vendored from jal-bot's code/gflights_searcher.py (2026-07-23) — see
docs/superpowers/specs/2026-07-23-scraping-rewrite-design.md. jal-bot's public
entry point took a fixed 2-leg open-jaw shape (out_date, ret_date, iata dict);
generalized here to an arbitrary ordered `legs: list[(dep, arr, date)]`, since
flight-bot's Asia Grand Tour route is a 3-leg multi-city trip, not a 2-leg
open-jaw. Live-verified 2026-07-23 that the aria-label format and parsing hold
for a real 3-leg trip, not just the 2-leg case. Parsing logic itself
(`parse_all_options`/`pick_cheapest_nonstop`/`pick_cheapest_alternative`) is
unchanged.
"""
import re
from datetime import date

from code.gflights import gflights_url

# Full summary card: "From $X total. <Nonstop|N stop> flight with <airline>.
# Leaves <airport> at <time> on <weekday, month day> and arrives at <airport>
# at <time> on <weekday, month day>[ (+1 day)]. Total duration <Xhr Ymin>. "
# [ Layover (i of n) is a <Zmin> layover at <airport> in <city>[. ...more]]
_CARD_RE = re.compile(
    r"^From ([\d,]+) US dollars total\.\s*(Nonstop|\d+ stop) flight with ([^.]+)\.\s*"
    r"Leaves ([^.]+?) at ([\d:]+\s*[AP]M) on ([^.]+?) and arrives at ([^.]+?) "
    r"at ([\d:]+\s*[AP]M) on ([^.]+?)\.\s*Total duration ([^.]+)\."
)
_LAYOVER_RE = re.compile(
    r"Layover \(1 of 1\) is an? ([^.]+?) layover at ([^.]+?) in "
)


def _duration_to_min(text: str) -> int | None:
    """'15 hr 45 min' / '14 hr' / '45 min' -> minutes."""
    h = re.search(r"(\d+)\s*hr", text)
    m = re.search(r"(\d+)\s*min", text)
    if not h and not m:
        return None
    return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)


def parse_all_options(aria_labels: list) -> list:
    """Parse every fare-card summary aria-label into a structured option.

    Each option: {price, currency, airline, stops ("Nonstop" or "N stop"),
    dep_airport, dep_time, dep_date, arr_airport, arr_time, arr_date,
    duration_min, layover_min, layover_airport}. `stops`/`airline`/duration
    describe the FIRST leg of the search only; price is the whole trip's
    total. Sorted cheapest-first. Malformed/unrelated labels are skipped.
    """
    options = []
    for label in aria_labels:
        if not label:
            continue
        m = _CARD_RE.match(label)
        if not m:
            continue
        lay = _LAYOVER_RE.search(label)
        options.append({
            "price": float(m.group(1).replace(",", "")),
            "currency": "USD",
            "airline": m.group(3),
            "stops": m.group(2),
            "dep_airport": m.group(4),
            "dep_time": m.group(5),
            "dep_date": m.group(6),
            "arr_airport": m.group(7),
            "arr_time": m.group(8),
            "arr_date": m.group(9),
            "duration_min": _duration_to_min(m.group(10)),
            "layover_min": _duration_to_min(lay.group(1)) if lay else None,
            "layover_airport": lay.group(2) if lay else None,
        })
    return sorted(options, key=lambda o: o["price"])


def pick_cheapest_nonstop(options: list) -> dict | None:
    """From an already-parsed options list, return the cheapest NONSTOP-first-leg
    one as {"price", "currency", "airline"}, or None if none present."""
    nonstop = [o for o in options if o["stops"] == "Nonstop"]
    if not nonstop:
        return None
    cheapest = min(nonstop, key=lambda o: o["price"])
    return {"price": cheapest["price"], "currency": "USD", "airline": cheapest["airline"]}


def pick_cheapest_alternative(options: list) -> dict | None:
    """From an already-parsed options list, return the cheapest CONNECTING
    (non-nonstop) option, or None if every option is nonstop / there are none."""
    connecting = [o for o in options if o["stops"] != "Nonstop"]
    return min(connecting, key=lambda o: o["price"]) if connecting else None


def parse_nonstop_cheapest(aria_labels: list) -> dict | None:
    """From a page's fare-card aria-labels, return the cheapest NONSTOP-first-leg
    fare as {"price": float, "currency": "USD", "airline": str}, or None."""
    return pick_cheapest_nonstop(parse_all_options(aria_labels))


def _dismiss_consent(page):
    for text in ("Accept all", "Reject all", "I agree", "Accept"):
        try:
            btn = page.get_by_role("button", name=text)
            if btn.count():
                btn.first.click(timeout=3000)
                page.wait_for_timeout(1200)
                return
        except Exception:
            pass


def _load_result_labels(page, legs, seat: int, adults: int) -> list:
    """Open the Google Flights search for these legs and return every fare
    card's summary aria-label, unparsed. legs: list of
    (dep_iata, arr_iata, 'YYYY-MM-DD')."""
    url = gflights_url(legs, seat=seat, adults=adults)
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    _dismiss_consent(page)
    try:
        page.wait_for_selector('[aria-label*="dollar" i]', timeout=25_000)
    except Exception:
        page.wait_for_timeout(6_000)
    page.wait_for_timeout(2_000)  # let all result cards render, not just the first batch

    return page.eval_on_selector_all(
        '[aria-label*="dollar" i]',
        "els => els.map(e => e.getAttribute('aria-label'))",
    )


def search_all_options(page, legs, seat: int = 1, adults: int = 1) -> list:
    """Return every outbound option (nonstop and connecting) for this
    itinerary, cheapest first. legs: list of (dep_iata, arr_iata, 'YYYY-MM-DD'),
    2 legs for a round trip, 3+ for multi-city."""
    labels = _load_result_labels(page, legs, seat, adults)
    options = parse_all_options(labels)
    for o in options:
        o["legs"] = [{"dep": l[0], "arr": l[1], "date": l[2]} for l in legs]
    return options
