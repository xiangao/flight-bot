import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


@dataclass
class AlertResult:
    should_alert: bool
    current_price: float
    avg_price: float
    pct_below: float  # fraction: 0.13 means 13% cheaper than avg; 0.0 if no history


def load_recent_prices(csv_path: Path, route_name: str, days: int = 7) -> list[float]:
    if not csv_path.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days)
    prices = []
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            if row["route"] != route_name:
                continue
            if datetime.fromisoformat(row["timestamp"]) >= cutoff:
                prices.append(float(row["cheapest_price"]))
    return prices


def analyze(
    csv_path: Path,
    route_name: str,
    current_price: float,
    threshold: float = 0.10,
) -> AlertResult:
    recent = load_recent_prices(csv_path, route_name)
    if len(recent) < 7:
        return AlertResult(
            should_alert=True,
            current_price=current_price,
            avg_price=0.0,
            pct_below=0.0,
        )
    avg = sum(recent) / len(recent)
    pct_below = (avg - current_price) / avg
    return AlertResult(
        should_alert=pct_below >= threshold,
        current_price=current_price,
        avg_price=avg,
        pct_below=pct_below,
    )
