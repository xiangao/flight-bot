import yaml
from pathlib import Path

from code.analyzer import analyze
from code.notifier import FlightResult, append_to_csv, write_summary, send_route_notification
from code.searcher import search_round_trip, search_multi_city

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config" / "routes.yaml"
CSV_PATH = BASE_DIR / "data" / "prices.csv"
OUTPUT_PATH = BASE_DIR / "output" / "latest.txt"


def main() -> None:
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        search_cfg = cfg["search"]
        routes = cfg["routes"]
    except (FileNotFoundError, KeyError, TypeError) as e:
        print(f"ERROR: Could not load config from {CONFIG_PATH}: {e}")
        return

    # SerpAPI stops param: 1=nonstop only, 2=1 stop or fewer
    STOP_SEARCHES = [(1, "nonstop"), (2, "1 stop")]

    results: list[FlightResult] = []
    alerts: list = []

    for route in routes:
        print(f"Searching {route['name']}...")
        search_fn = search_round_trip if route["type"] == "round_trip" else search_multi_city
        route_pairs = []

        for stops_filter, label in STOP_SEARCHES:
            try:
                offer = search_fn(route, search_cfg, stops_filter=stops_filter)
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
            )
            alert = analyze(
                CSV_PATH, route["name"], offer.price,
                search_cfg["alert_threshold"], stops=offer.stops,
            )
            append_to_csv(CSV_PATH, result)
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
