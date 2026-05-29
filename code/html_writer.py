import csv
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus


_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       background: #f0f4fa; color: #222; padding: 24px; }
h1 { font-size: 1.3rem; font-weight: 600; margin-bottom: 4px; }
.meta { font-size: 0.85rem; color: #666; margin-bottom: 28px; }

/* ── Card ──────────────────────────────────────────── */
.card { background: #fff; border-radius: 10px;
        box-shadow: 0 1px 4px rgba(0,0,0,.09); margin-bottom: 24px; overflow: hidden; }
.card-header { padding: 16px 20px 12px;
               border-bottom: 1px solid #f1f5f9; }
.route-name { font-size: 0.8rem; font-weight: 700; text-transform: uppercase;
              letter-spacing: 0.07em; color: #64748b; }
.card-body { padding: 16px 20px 20px; }

/* ── Two-column panel grid ─────────────────────────── */
.panels { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 640px) { .panels { grid-template-columns: 1fr; } }

.panel { border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px 16px; }
.panel.alert  { border-color: #86efac; background: #f0fdf4; }
.panel.stale  { border-color: #fde68a; background: #fffbeb; }
.panel.empty  { border-color: #e2e8f0; background: #f8fafc; }
.panel-stop   { font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
                letter-spacing: 0.08em; color: #94a3b8; margin-bottom: 8px; }

/* ── Price ─────────────────────────────────────────── */
.price-row  { display: flex; align-items: baseline; gap: 8px; margin-bottom: 4px; }
.price      { font-size: 1.9rem; font-weight: 700; color: #111; }
.badge      { font-size: 0.72rem; border-radius: 4px; padding: 2px 7px; font-weight: 600; }
.badge-alert { background: #dcfce7; color: #15803d; }
.badge-stale { background: #fef3c7; color: #92400e; }
.dates      { font-size: 0.85rem; color: #64748b; margin-bottom: 12px; }

/* ── Segment timeline ──────────────────────────────── */
.leg { margin-top: 10px; }
.leg-header { font-size: 0.72rem; font-weight: 600; text-transform: uppercase;
              letter-spacing: 0.06em; color: #94a3b8; margin-bottom: 6px; }
.seg { display: flex; flex-direction: column; gap: 2px; padding: 8px 10px;
       background: #f8fafc; border-radius: 6px; margin-bottom: 4px; }
.seg-airports { font-size: 1.0rem; font-weight: 600; color: #1e293b; }
.seg-times    { font-size: 0.82rem; color: #475569; }
.seg-meta     { font-size: 0.78rem; color: #94a3b8; }
.layover      { font-size: 0.78rem; color: #f59e0b; font-weight: 600;
                padding: 3px 0 3px 10px; }

/* ── History table ─────────────────────────────────── */
h3 { font-size: 0.78rem; font-weight: 600; color: #64748b; text-transform: uppercase;
     letter-spacing: 0.05em; margin: 20px 0 8px;
     border-top: 1px solid #e2e8f0; padding-top: 14px; }
table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
th { text-align: left; color: #94a3b8; font-weight: 500;
     padding: 4px 8px 6px; border-bottom: 1px solid #e2e8f0; }
td { padding: 5px 8px; border-bottom: 1px solid #f1f5f9; color: #475569; }
td.price-cell { font-weight: 600; color: #111; }
td.low        { color: #16a34a; font-weight: 700; }
td.empty      { color: #cbd5e1; }
td.dates-cell { white-space: nowrap; }
td.leg-cell   { white-space: nowrap; color: #475569; }
.hist-wrap    { overflow-x: auto; }
table a       { color: #2563eb; text-decoration: none; font-weight: 600; }
table a:hover { text-decoration: underline; }
"""


def _duration_label(minutes: int | None) -> str:
    if not minutes:
        return ""
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}m" if m else f"{h}h"


def _load_history(csv_path: Path, route: str, days: int = 90) -> list[dict]:
    """Return rows for this route (newest first) within `days` days."""
    if not csv_path.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days)
    rows = []
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("route") != route:
                continue
            try:
                ts = datetime.fromisoformat(row["timestamp"])
                if ts >= cutoff:
                    rows.append({
                        "ts": ts,
                        "date": ts.strftime("%b %d"),
                        "price": float(row["cheapest_price"]),
                        "currency": row.get("currency", "USD"),
                        "airline": row.get("airline", ""),
                        "departure": row.get("departure_date", ""),
                        "return": row.get("final_leg_date", ""),
                        "stops": int(row.get("stops", -1)),
                        "details": row.get("details", ""),
                    })
            except (ValueError, KeyError):
                continue

    # Deduplicate per (date, stops) — keep lowest price
    by_date_stops: dict = {}
    for r in rows:
        key = (r["date"], r["stops"])
        if key not in by_date_stops or r["price"] < by_date_stops[key]["price"]:
            by_date_stops[key] = r
    # Sort by real timestamp (newest first) — the "%b %d" label is not chronological
    return sorted(by_date_stops.values(), key=lambda r: r["ts"], reverse=True)


def _parse_legs(details: str) -> dict:
    """Parse the CSV `details` itinerary text into per-leg duration + stops.

    Returns ``{'outbound': {...}, 'inbound': {...}}`` where each value is
    ``{'dur_min': int, 'via': [codes], 'stop_count': int}``. Missing legs are
    absent (e.g. the multi-city CSV has no details at all → empty dict).
    """
    legs: dict = {}
    if not details:
        return legs
    # Split into chunks each beginning with an "Outbound:" / "Inbound:" header
    for part in re.split(r"\n(?=(?:Outbound|Inbound):)", details.strip()):
        m = re.match(
            r"(Outbound|Inbound):\s*.*?,\s*(\d+)\s*stop\(s\)"
            r"(?:,\s*(\d+)h(?:\s*(\d+)m)?)?",
            part,
        )
        if not m:
            continue
        h = int(m.group(3)) if m.group(3) else 0
        mm = int(m.group(4)) if m.group(4) else 0
        # Connection airports = arrival of every segment except the last
        seg_pairs = re.findall(r"\b([A-Z]{3})\b[^\n]*?->\s*([A-Z]{3})\b", part)
        via = [arr for _, arr in seg_pairs[:-1]] if len(seg_pairs) > 1 else []
        legs[m.group(1).lower()] = {
            "dur_min": h * 60 + mm,
            "via": via,
            "stop_count": int(m.group(2)),
        }
    return legs


def _leg_summary(leg: dict | None) -> str:
    """'via LAX · 21h 30m', or 'nonstop · 15h 15m', or '—'."""
    if not leg:
        return "—"
    dur = _duration_label(leg["dur_min"])
    where = f"via {', '.join(leg['via'])}" if leg["via"] else "nonstop"
    return f"{where} · {dur}" if dur else where


def _fmt_travel_dates(departure: str, ret: str) -> str:
    """'2026-09-01','2026-09-22' → 'Sep 01 → Sep 22'."""
    def short(d: str) -> str:
        try:
            return datetime.fromisoformat(d).strftime("%b %d")
        except (ValueError, TypeError):
            return d or ""
    dep = short(departure)
    out = short(ret)
    if dep and out:
        return f"{dep} → {out}"
    return dep or out or "—"


def _airport_codes_from_details(details: str, route_cfg: dict) -> tuple[str, str]:
    """Best-effort (origin, outbound-destination) airport codes for a history row.

    Primary source is the itinerary text in the CSV `details` column (ground
    truth for the row); the outbound block's first and last 3-letter codes are
    the trip's origin and turnaround airport. Falls back to the route config's
    origin when `details` is absent (e.g. the multi-city CSV has no details).
    """
    if details:
        outbound = details.split("Inbound")[0]
        codes = re.findall(r"\b[A-Z]{3}\b", outbound)
        if len(codes) >= 2:
            return codes[0], codes[-1]
    # Fallback: route config origin; destination unknown → no link
    origin = route_cfg.get("origin", "")
    if not origin and route_cfg.get("segments"):
        origin = route_cfg["segments"][0].get("origin", "")
    return origin, ""


def _gflights_link(origin: str, dest: str, departure: str, ret: str) -> str:
    """Deterministic Google Flights search URL for the row's route + dates.

    No booking API here returns a real deep link, so we synthesise a search
    that lands on live results for those exact airports and dates — and, being
    derived purely from the row, it stays valid for every historical entry.
    """
    if not (origin and dest and departure):
        return ""
    q = f"Flights from {origin} to {dest} on {departure}"
    if ret:
        q += f" returning {ret}"
    return "https://www.google.com/travel/flights?q=" + quote_plus(q)


def _history_table(rows: list[dict], route_cfg: dict, stops: int) -> str:
    """Render one history section (heading + table) for a single stop count.

    Returns "" when there is no history for this stop count, so the caller can
    omit the section entirely. The `stops` filter is on the CSV `stops` column,
    which is the *outbound* stop count; both leg summaries are shown so the
    inbound (which may differ) stays visible.
    """
    sub = [r for r in rows if r["stops"] == stops]
    if not sub:
        return ""

    label = "Nonstop" if stops == 0 else f"{stops} Stop" + ("s" if stops > 1 else "")
    lo = min(r["price"] for r in sub)

    html = [
        f"<h3>{label} — Price History</h3>",
        '<div class="hist-wrap"><table><tr>'
        "<th>Date</th><th>Price</th><th>Airline</th><th>Travel dates</th>"
        "<th>Outbound</th><th>Inbound</th><th>Total</th><th></th></tr>",
    ]
    for r in sub[:30]:  # cap at 30 rows
        legs = _parse_legs(r.get("details", ""))
        out, inb = legs.get("outbound"), legs.get("inbound")
        total_min = (out["dur_min"] if out else 0) + (inb["dur_min"] if inb else 0)
        total = _duration_label(total_min) if total_min else "—"

        cls = 'class="price-cell low"' if r["price"] <= lo else 'class="price-cell"'
        origin, dest = _airport_codes_from_details(r.get("details", ""), route_cfg)
        link_url = _gflights_link(origin, dest, r["departure"], r["return"])
        link = (
            f'<a href="{link_url}" target="_blank" rel="noopener">Search ↗</a>'
            if link_url else ""
        )
        html.append(
            "<tr>"
            f"<td>{r['date']}</td>"
            f"<td {cls}>${r['price']:,.0f}</td>"
            f"<td>{r['airline']}</td>"
            f"<td class=\"dates-cell\">{_fmt_travel_dates(r['departure'], r['return'])}</td>"
            f"<td class=\"leg-cell\">{_leg_summary(out)}</td>"
            f"<td class=\"leg-cell\">{_leg_summary(inb)}</td>"
            f"<td class=\"dates-cell\">{total}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )
    html.append("</table></div>")
    return "\n".join(html)


def _render_segment_timeline(segments: list, duration_min: int | None, leg_label: str) -> str:
    if not segments:
        return ""
    dur = _duration_label(duration_min)
    header_extra = f"  ·  {dur}" if dur else ""
    parts = [f'<div class="leg-header">{leg_label}{header_extra}</div>']
    for seg in segments:
        aircraft = f"  ·  {seg.aircraft}" if seg.aircraft else ""
        flight_meta = f"{seg.flight}{aircraft}" if seg.flight else (seg.aircraft or "")
        parts.append(
            f'<div class="seg">'
            f'<div class="seg-airports">{seg.from_airport} → {seg.to_airport}</div>'
            f'<div class="seg-times">{seg.dep_local}  →  {seg.arr_local}</div>'
            + (f'<div class="seg-meta">{flight_meta}  ·  {_duration_label(seg.duration_min)}</div>' if flight_meta or seg.duration_min else "")
            + f'</div>'
        )
        if seg.layover_min > 0:
            parts.append(
                f'<div class="layover">⏱ Layover in {seg.to_airport}: {_duration_label(seg.layover_min)}</div>'
            )
    return f'<div class="leg">{"".join(parts)}</div>'


def _render_stop_panel(
    stop_count: int,
    result,
    alert,
    history: list[dict],
) -> str:
    label = "Nonstop" if stop_count == 0 else f"{stop_count} Stop{'s' if stop_count > 1 else ''}"

    if result is not None:
        is_alert = alert and alert.should_alert
        panel_cls = "panel alert" if is_alert else "panel"
        badge = '<span class="badge badge-alert">★ Great price</span>' if is_alert else ""
        price_fmt = f"${result.cheapest_price:,.0f}"
        dates = f"{result.departure_date} → {result.final_leg_date}" if result.final_leg_date else result.departure_date

        out_timeline = _render_segment_timeline(
            result.outbound_segments, result.outbound_duration_min, "Outbound"
        )
        inb_timeline = _render_segment_timeline(
            result.inbound_segments, result.inbound_duration_min, "Inbound"
        )
        if not out_timeline:
            # Fallback: show airline text when no structured segments
            out_timeline = f'<div class="leg"><div class="seg"><div class="seg-airports">{result.airline}</div></div></div>'

        return f"""<div class="{panel_cls}">
  <div class="panel-stop">{label}</div>
  <div class="price-row"><span class="price">{price_fmt}</span>{badge}</div>
  <div class="dates">{dates}</div>
  {out_timeline}
  {inb_timeline}
</div>"""

    # No live result — show last known for this stop count
    hist = [r for r in history if r["stops"] == stop_count]
    if hist:
        last = hist[0]
        badge = f'<span class="badge badge-stale">Last seen {last["date"]}</span>'
        price_fmt = f"${last['price']:,.0f}"
        dates = f"{last['departure']} → {last['return']}" if last["return"] else last["departure"]
        return f"""<div class="panel stale">
  <div class="panel-stop">{label}</div>
  <div class="price-row"><span class="price">{price_fmt}</span>{badge}</div>
  <div class="dates">{dates}</div>
  <div class="dates" style="color:#94a3b8">{last['airline']}</div>
</div>"""

    return f"""<div class="panel empty">
  <div class="panel-stop">{label}</div>
  <p style="color:#cbd5e1;font-size:0.85rem;margin-top:8px">No data</p>
</div>"""


def _render_card(
    route_name: str,
    results: dict,        # {stop_count: FlightResult}
    alerts: dict,         # {stop_count: AlertResult}
    history: list[dict],
    route_cfg: dict,
    min_stops: int = 0,
    max_stops: int = 1,
) -> str:
    panel_0 = _render_stop_panel(0, results.get(0), alerts.get(0), history) if min_stops <= 0 <= max_stops else ""
    panel_1 = _render_stop_panel(1, results.get(1), alerts.get(1), history) if min_stops <= 1 <= max_stops else ""

    # Skip card entirely if no live data and no history
    if not results and not history:
        return ""

    hist_html = _history_table(history, route_cfg, 0) + _history_table(history, route_cfg, 1)
    if not hist_html:
        hist_html = "<h3>Price History</h3><p style='color:#aaa;font-size:0.85rem'>No history yet.</p>"
    return f"""<div class="card">
  <div class="card-header">
    <div class="route-name">{route_name}</div>
  </div>
  <div class="card-body">
    <div class="panels">
      {panel_0}
      {panel_1}
    </div>
    {hist_html}
  </div>
</div>"""


def write_html(
    route_configs: list[dict],
    results_by_route: dict,   # route_name → {stop_count: FlightResult}
    alerts_by_route: dict,    # route_name → {stop_count: AlertResult}
    csv_path_by_route: dict,
    csv_name_by_route: dict,
    html_path: Path,
) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cards = []
    for route_cfg in route_configs:
        name = route_cfg["name"]
        csv_name = csv_name_by_route.get(name, name)
        route_results = results_by_route.get(name, {})
        route_alerts = alerts_by_route.get(name, {})
        csv_path = csv_path_by_route.get(name)
        history = _load_history(csv_path, csv_name) if csv_path else []
        min_stops = int(route_cfg.get("min_stops", 0))
        max_stops = int(route_cfg.get("max_stops", 1))
        card = _render_card(name, route_results, route_alerts, history, route_cfg, min_stops=min_stops, max_stops=max_stops)
        if card:
            cards.append(card)

    body = "\n".join(cards) if cards else "<p style='color:#888'>No data yet.</p>"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flight Prices</title>
<style>{_CSS}</style>
</head>
<body>
<h1>Flight Prices</h1>
<p class="meta">Updated {now}</p>
{body}
</body>
</html>"""

    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html)
