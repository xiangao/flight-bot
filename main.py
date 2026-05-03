import yaml
from pathlib import Path

from code.analyzer import analyze
from code.notifier import FlightResult, append_to_csv, write_summary, send_desktop_notification
from code.searcher import search_round_trip, search_multi_city

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config" / "routes.yaml"
CSV_PATH = BASE_DIR / "data" / "prices.csv"
OUTPUT_PATH = BASE_DIR / "output" / "latest.txt"


def main() -> None:
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    search_cfg = cfg["search"]
    results: list[FlightResult] = []
    alerts = []

    for route in cfg["routes"]:
        print(f"Searching {route['name']}...")
        try:
            offer = (
                search_round_trip(route, search_cfg)
                if route["type"] == "round_trip"
                else search_multi_city(route, search_cfg)
            )
        except Exception as e:
            print(f"ERROR searching {route['name']}: {e}")
            continue

        if offer is None:
            print(f"  No results found for {route['name']}")
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
        alert = analyze(CSV_PATH, route["name"], offer.price, search_cfg["alert_threshold"])
        append_to_csv(CSV_PATH, result)
        results.append(result)
        alerts.append(alert)

        if alert.should_alert:
            send_desktop_notification(result, alert)
            print(f"  ${offer.price:,.0f} — ALERT sent")
        else:
            print(f"  ${offer.price:,.0f} — logged (no alert)")

    if results:
        write_summary(OUTPUT_PATH, results, alerts)
        print(f"\nSummary written to {OUTPUT_PATH}")
    else:
        print("\nNo results to report.")


if __name__ == "__main__":
    main()
