import os
import yaml
from pathlib import Path

from code.analyzer import analyze
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

    for route in routes:
        route_search_cfg = dict(search_cfg)
        route_search_cfg.update(route.get("search", {}))
        if "provider" in route:
            route_search_cfg["provider"] = route["provider"]

        provider = str(route_search_cfg.get("provider") or os.environ.get("FLIGHT_PROVIDER", "serpapi")).lower()
        max_stops = int(route.get("max_stops", route_search_cfg.get("max_stops", 1)))
        # SerpAPI uses 2 for "1 stop or fewer"; Ignav uses max_stops directly.
        stops_filter = max_stops + 1 if provider == "serpapi" else max_stops

        print(f"Searching {route['name']}...")
        search_fn = search_round_trip if route["type"] == "round_trip" else search_multi_city
        csv_path = history_path_for(route)
        route_pairs = []

        label = "nonstop" if max_stops == 0 else f"{max_stops} stop" if max_stops == 1 else f"{max_stops} stops"
        try:
            offer = search_fn(route, route_search_cfg, stops_filter=stops_filter)
        except Exception as e:
            print(f"  ERROR ({label}): {e}")
            continue

        if offer is None:
            print(f"  No {label} results")
            continue

        result = FlightResult(
            route=route["name"],
            cheapest_price=offer.price,
            currency=offer.currency,
            departure_date=offer.departure_date,
            final_leg_date=offer.final_leg_date,
            stops=offer.stops,
            airline=offer.airline,
            details=offer.details,
        )
        alert = analyze(
            csv_path, route["name"], offer.price,
            route_search_cfg["alert_threshold"], stops=offer.stops,
        )
        append_to_csv(csv_path, result)
        results.append(result)
        alerts.append(alert)
        route_pairs.append((result, alert))
        flag = "  ** ALERT **" if alert.should_alert else ""
        print(f"  {label}: ${offer.price:,.0f}{flag}")

        if route_pairs and any(a.should_alert for _, a in route_pairs):
            send_route_notification(route["name"], route_pairs)

    if results:
        write_summary(OUTPUT_PATH, results, alerts)
        print(f"\nSummary written to {OUTPUT_PATH}")
    else:
        print("\nNo results to report.")


if __name__ == "__main__":
    main()
