#!/usr/bin/env python3
"""Regenerate data/dgca_fare_benchmark.csv — the LABELLED PLACEHOLDER benchmark.

This is not DGCA data and this script cannot make it so. It exists because the
back-test in pushpak/validate.py needs a file with the right columns to be
runnable and testable, and because that file has to cover whatever basket
config.BASKET_SIZE currently selects. Every row it writes carries
``provenance=ILLUSTRATIVE_PLACEHOLDER`` and ``is_official=0``, which is what
makes backtest() return verdict=MACHINERY_ONLY instead of claiming validation.

Two properties are deliberate:

  * **Not derived from Pushpak's own quotes.** The series is generated from an
    independent anchor (route distance) and its own random walk. Deriving it
    from the synthetic generator would make the back-test circular and would
    produce a flattering correlation that means nothing.
  * **Deterministic.** Seeded from the route id and date, so re-running gives
    byte-identical output and a reviewer can diff it.

To run a real back-test, overwrite the file with an official DGCA Tariff
Monitoring Unit extract using the same columns and set is_official=1.

    python3 scripts/build_benchmark_placeholder.py [--days 30] [--end-date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import hashlib
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pushpak import config                       # noqa: E402
from pushpak.collect.synthetic import route_distance_km   # noqa: E402

SOURCE_NOTE = (
    "NOT an official DGCA extract. Placeholder so the back-test machinery is "
    "runnable. Replace with the DGCA Tariff Monitoring Unit monthly fare "
    "release (see README section 6)."
)
PROVENANCE = "ILLUSTRATIVE_PLACEHOLDER"

# An average-fare level anchored on distance only. Intentionally a different
# functional form from the synthetic fare generator's, so the two series are
# not two views of the same model.
ANCHOR_FIXED_INR = 2450.0
ANCHOR_PER_KM = 2.4
DAILY_WALK_SIGMA = 0.018        # ~1.8% daily standard deviation
WALK_MEAN_REVERSION = 0.15      # pulls the walk back toward its anchor


def _rng(*parts: object) -> random.Random:
    key = "|".join(str(p) for p in parts).encode()
    return random.Random(int.from_bytes(hashlib.sha1(key).digest()[:8], "big"))


def build(end: date, days: int, routes: list[str]) -> pd.DataFrame:
    rows = []
    for route_id in routes:
        km = route_distance_km(route_id)
        anchor = ANCHOR_FIXED_INR + ANCHOR_PER_KM * (km if km is not None else 1200.0)
        level = 1.0
        for i in range(days - 1, -1, -1):
            d = end - timedelta(days=i)
            r = _rng("benchmark", route_id, d.isoformat())
            level += r.gauss(0.0, DAILY_WALK_SIGMA) - WALK_MEAN_REVERSION * (level - 1.0)
            rows.append({
                "period": d.isoformat(),
                "route_id": route_id,
                "avg_fare_inr": round(anchor * level, 0),
                "fare_class": config.INDEX_FARE_CLASS,
                "provenance": PROVENANCE,
                "is_official": 0,
                "source_note": SOURCE_NOTE,
            })
    return pd.DataFrame(rows).sort_values(["period", "route_id"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--end-date", default=None, help="YYYY-MM-DD (default: today)")
    args = ap.parse_args()

    end = (datetime.strptime(args.end_date, "%Y-%m-%d").date()
           if args.end_date else date.today())

    if not config.ROUTE_WEIGHTS_CSV.exists():
        raise SystemExit("run scripts/build_weights.py first")
    routes = list(pd.read_csv(config.ROUTE_WEIGHTS_CSV)["route_id"])

    df = build(end, args.days, routes)
    df.to_csv(config.DGCA_FARE_BENCHMARK_CSV, index=False)

    print(f"Wrote {config.DGCA_FARE_BENCHMARK_CSV}")
    print(f"{len(df):,} rows | {len(routes)} routes | "
          f"{(end - timedelta(days=args.days - 1))} .. {end}")
    print(f"provenance={PROVENANCE}  is_official=0")
    print("backtest() will return verdict=MACHINERY_ONLY against this file.")


if __name__ == "__main__":
    main()
