#!/usr/bin/env python3
"""Derive Pushpak route weights from the official DGCA city-pair traffic release.

The brief says: *do not invent the weighting.*  So this script is the only
thing allowed to produce data/route_weights.csv, and it reads exclusively from
data/dgca_domestic_city_pair_traffic.csv (DGCA monthly domestic city-pair
passenger statistics).  Re-run it whenever a newer DGCA release is dropped in.

    python3 scripts/build_weights.py [--months 12] [--top 5]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pushpak import config  # noqa: E402


def build(months: int, top: int) -> pd.DataFrame:
    src = config.DGCA_TRAFFIC_CSV
    if not src.exists():
        raise SystemExit(f"DGCA source file missing: {src}")

    df = pd.read_csv(src)
    required = {"Year", "Month", "City1", "City2", "PaxToCity2", "PaxFromCity2"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"DGCA file is missing columns: {sorted(missing)}")

    df["period"] = df["Year"].astype(int) * 100 + df["Month"].astype(int)

    # Normalisation step 1: collapse DGCA's mid-series airport renames so a
    # single metro is not split across labels (see config.DGCA_CITY_ALIASES).
    for col in ("City1", "City2"):
        df[col] = df[col].astype(str).str.strip().replace(config.DGCA_CITY_ALIASES)

    periods = sorted(df["period"].unique())[-months:]
    win = df[df["period"].isin(periods)].copy()

    # Normalisation step 2: a fare index treats a city pair as undirected, but
    # DGCA reports directional pax. Sum both directions into one canonical pair.
    win["pax"] = win["PaxToCity2"].fillna(0) + win["PaxFromCity2"].fillna(0)
    win["city_a"], win["city_b"] = zip(
        *[tuple(sorted((a, b))) for a, b in zip(win["City1"], win["City2"])]
    )
    win = win[win["city_a"] != win["city_b"]]

    grouped = (
        win.groupby(["city_a", "city_b"], as_index=False)["pax"]
        .sum()
        .sort_values("pax", ascending=False)
    )
    national_total = float(win["pax"].sum())

    basket = grouped.head(top).copy()
    basket["national_share"] = basket["pax"] / national_total
    basket["weight"] = basket["pax"] / basket["pax"].sum()   # normalised to 1.0
    basket["iata_a"] = basket["city_a"].map(config.IATA)
    basket["iata_b"] = basket["city_b"].map(config.IATA)

    unmapped = basket[basket["iata_a"].isna() | basket["iata_b"].isna()]
    if len(unmapped):
        raise SystemExit(
            "No IATA mapping for: "
            + ", ".join(f"{r.city_a}/{r.city_b}" for r in unmapped.itertuples())
            + "  -> add them to config.IATA"
        )

    basket["route_id"] = [
        "-".join(sorted((a, b))) for a, b in zip(basket["iata_a"], basket["iata_b"])
    ]
    basket["traffic_window"] = f"{periods[0]}..{periods[-1]}"
    basket["national_pax_in_window"] = national_total
    basket["provenance"] = config.ROUTE_WEIGHT_PROVENANCE
    basket["source_file"] = src.name

    return basket[
        [
            "route_id", "city_a", "city_b", "iata_a", "iata_b",
            "pax", "weight", "national_share",
            "traffic_window", "national_pax_in_window", "provenance", "source_file",
        ]
    ].rename(columns={"pax": "annual_pax"})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--months", type=int, default=config.TRAFFIC_WINDOW_MONTHS)
    ap.add_argument("--top", type=int, default=config.BASKET_SIZE)
    args = ap.parse_args()

    basket = build(args.months, args.top)
    config.ROUTE_WEIGHTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    basket.to_csv(config.ROUTE_WEIGHTS_CSV, index=False)

    print(f"Wrote {config.ROUTE_WEIGHTS_CSV}")
    print(f"Traffic window: {basket['traffic_window'].iloc[0]}")
    print()
    print(f"{'route':<10}{'pax (12m)':>14}{'basket wt':>12}{'natl share':>12}")
    for r in basket.itertuples():
        print(f"{r.route_id:<10}{r.annual_pax:>14,.0f}{r.weight:>11.4%}{r.national_share:>12.2%}")
    print(f"{'TOTAL':<10}{basket['annual_pax'].sum():>14,.0f}"
          f"{basket['weight'].sum():>11.4%}{basket['national_share'].sum():>12.2%}")


if __name__ == "__main__":
    main()
