import csv
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from code.analyzer import AlertResult


@dataclass
class FlightResult:
    route: str
    cheapest_price: float
    currency: str
    departure_date: str
    final_leg_date: str
    stops: int
    airline: str


_CSV_FIELDS = [
    "timestamp", "route", "cheapest_price", "currency",
    "departure_date", "final_leg_date", "stops", "airline",
]


def append_to_csv(csv_path: Path, result: FlightResult) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "route": result.route,
            "cheapest_price": result.cheapest_price,
            "currency": result.currency,
            "departure_date": result.departure_date,
            "final_leg_date": result.final_leg_date,
            "stops": result.stops,
            "airline": result.airline,
        })


def write_summary(
    output_path: Path,
    results: list[FlightResult],
    alerts: list[AlertResult],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"Flight Price Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
    for result, alert in zip(results, alerts):
        lines.append(f"\n{result.route}")
        lines.append(f"  Price:    ${result.cheapest_price:,.2f} {result.currency}")
        lines.append(f"  Dates:    {result.departure_date} → {result.final_leg_date}")
        lines.append(f"  Airline:  {result.airline}  |  Stops: {result.stops}")
        if alert.avg_price > 0:
            lines.append(
                f"  Avg(7d):  ${alert.avg_price:,.2f}  ({alert.pct_below * 100:+.1f}%)"
            )
        if alert.should_alert:
            lines.append("  ** ALERT: Notable price! **")
    output_path.write_text("\n".join(lines) + "\n")


def send_desktop_notification(result: FlightResult, alert: AlertResult) -> None:
    if not shutil.which("notify-send"):
        print("WARNING: notify-send not available, skipping desktop notification")
        return
    if alert.avg_price > 0:
        body = (
            f"${result.cheapest_price:,.0f} — {alert.pct_below * 100:.0f}% below"
            f" recent avg (${alert.avg_price:,.0f})\n"
            f"Best dates: {result.departure_date} → {result.final_leg_date}"
            f"  |  {result.airline}, {result.stops} stop(s)"
        )
    else:
        body = (
            f"${result.cheapest_price:,.0f} {result.currency}\n"
            f"Best dates: {result.departure_date} → {result.final_leg_date}"
            f"  |  {result.airline}, {result.stops} stop(s)"
        )
    subprocess.run(
        ["notify-send", f"✈ {result.route}", body, "--urgency=normal"],
        check=False,
    )
