import os
import shutil
import subprocess
import yaml
from pathlib import Path

from code.analyzer import analyze
from code.html_writer import write_html
from code.notifier import FlightResult, append_to_csv, write_summary, send_route_notification
from code.searcher import search_round_trip, search_multi_city

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config" / "routes.yaml"
MULTI_CITY_CSV_PATH = BASE_DIR / "data" / "prices.csv"
ROUND_TRIP_CSV_PATH = BASE_DIR / "data" / "round_trip_prices.csv"
OUTPUT_PATH = BASE_DIR / "output" / "latest.txt"


def history_path_for(route: dict) -> Path:
    return ROUND_TRIP_CSV_PATH if route["type"] == "round_trip" else MULTI_CITY_CSV_PATH


def main() -> None:
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        search_cfg = cfg["search"]
        routes = cfg["routes"]
    except (FileNotFoundError, KeyError, TypeError) as e:
        print(f"ERROR: Could not load config from {CONFIG_PATH}: {e}")
        return

    results: list[FlightResult] = []
    alerts: list = []
    results_by_route: dict = {}   # route_name → {stop_count: FlightResult}
    alerts_by_route: dict = {}    # route_name → {stop_count: AlertResult}
    csv_name_by_route: dict = {r["name"]: r.get("csv_name", r["name"]) for r in routes}
    csv_path_by_route: dict = {r["name"]: history_path_for(r) for r in routes}

    for route in routes:
        route_search_cfg = dict(search_cfg)
        route_search_cfg.update(route.get("search", {}))
        if "provider" in route:
            route_search_cfg["provider"] = route["provider"]

        search_fn = search_round_trip if route["type"] == "round_trip" else search_multi_city
        csv_path = history_path_for(route)
        csv_name = csv_name_by_route[route["name"]]

        print(f"Searching {route['name']}...")
        try:
            offers_by_stops = search_fn(route, route_search_cfg)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        route_pairs = []
        route_results: dict = {}
        route_alerts: dict = {}

        for stop_count in sorted(offers_by_stops.keys()):
            offer = offers_by_stops[stop_count]
            label = "nonstop" if stop_count == 0 else f"{stop_count} stop"
            if offer is None:
                print(f"  No {label} results")
                continue

            result = FlightResult(
                route=csv_name,
                cheapest_price=offer.price,
                currency=offer.currency,
                departure_date=offer.departure_date,
                final_leg_date=offer.final_leg_date,
                stops=stop_count,
                airline=offer.airline,
                details=offer.details,
                outbound_segments=offer.outbound_segments,
                inbound_segments=offer.inbound_segments,
                outbound_duration_min=offer.outbound_duration_min,
                inbound_duration_min=offer.inbound_duration_min,
            )
            alert = analyze(
                csv_path, csv_name, offer.price,
                route_search_cfg["alert_threshold"], stops=stop_count,
            )
            append_to_csv(csv_path, result)
            results.append(result)
            alerts.append(alert)
            route_results[stop_count] = result
            route_alerts[stop_count] = alert
            route_pairs.append((result, alert))

            flag = "  ** ALERT **" if alert.should_alert else ""
            print(f"  {label}: ${offer.price:,.0f}{flag}")

        if route_results:
            results_by_route[route["name"]] = route_results
            alerts_by_route[route["name"]] = route_alerts

        if route_pairs and any(a.should_alert for _, a in route_pairs):
            send_route_notification(route["name"], route_pairs)

    html_path = BASE_DIR / "output" / "listings.html"
    write_html(routes, results_by_route, alerts_by_route, csv_path_by_route, csv_name_by_route, html_path)
    _deploy(html_path)

    if results:
        write_summary(OUTPUT_PATH, results, alerts)
        print(f"\nSummary written to {OUTPUT_PATH}")
    else:
        print("\nNo live results — history-only page deployed.")


def _deploy(html_path: Path) -> None:
    site_dir = BASE_DIR / "site"
    if not site_dir.exists():
        print("  WARNING: site/ dir missing — skipping deploy")
        return
    shutil.copy(html_path, site_dir / "index.html")
    try:
        subprocess.run(["git", "add", "index.html"], cwd=site_dir, check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=site_dir).returncode == 0:
            print("  Site unchanged — skipping push")
            return
        subprocess.run(
            ["git", "commit", "-m", "Update flight prices"],
            cwd=site_dir, check=True, capture_output=True,
        )
        subprocess.run(["git", "push", "-u", "origin", "gh-pages"], cwd=site_dir, check=True, capture_output=True)
        print("  Site deployed to GitHub Pages")
    except subprocess.CalledProcessError as e:
        print(f"  WARNING: deploy failed: {e}")


if __name__ == "__main__":
    main()
