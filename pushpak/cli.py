"""Command-line entry point. Every component is demoable independently.

    python3 -m pushpak.cli robots-audit           # 1. show the ethical gate firing
    python3 -m pushpak.cli discover               # 1. let a search engine pick the sources
    python3 -m pushpak.cli collect --mode hybrid  # 1. collect quotes
    python3 -m pushpak.cli seed --days 30         # 1. backfill a synthetic history
    python3 -m pushpak.cli index                  # 3. compute + store the index
    python3 -m pushpak.cli explain --date ...     # 3. reconstruct one number by hand
    python3 -m pushpak.cli elementary             # 3. Dutot vs Jevons
    python3 -m pushpak.cli adjust                 # 3. chain drift + seasonality
    python3 -m pushpak.cli validate               # 6. run the back-test
    python3 -m pushpak.cli alerts                 # 1. operational alerts
    python3 -m pushpak.cli serve                  # 4+5. dashboard + API
    python3 -m pushpak.cli stats                  # data inventory
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta

import pandas as pd

from . import config, storage
from .collect.robots import RobotsGate
from .collect.runner import run_collection
from .collect.synthetic import generate_backfill
from .index import compute_index, explain, load_route_weights
from .validate import backtest


def _parse_date(s: str | None) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date() if s else date.today()


# ---------------------------------------------------------------------------
def cmd_robots_audit(args) -> int:
    gate = RobotsGate()
    rows = gate.snapshot()
    out = config.DATA_DIR / "robots_compliance_snapshot.json"
    out.write_text(json.dumps(
        {"generated_at": datetime.now().isoformat(timespec="seconds"),
         "user_agent": config.USER_AGENT,
         "policy": "fail-closed: unreachable robots.txt is treated as DISALLOWED",
         "results": rows}, indent=2))

    print(f"\nrobots.txt compliance audit  (UA: {config.USER_AGENT.split(' (')[0]})\n")
    print(f"{'source':<22}{'type':<9}{'verdict':<10}{'rule / reason'}")
    print("-" * 96)
    allowed = 0
    for r in rows:
        verdict = "ALLOW" if r["allowed"] else "REFUSE"
        allowed += bool(r["allowed"])
        print(f"{r['source']:<22}{r['source_type']:<9}{verdict:<10}"
              f"{(r['rule'] or r['reason'])[:52]}")
    print("-" * 96)
    print(f"{allowed}/{len(rows)} fare pages permitted for automated collection.")
    if allowed == 0:
        print(
            "\nNo configured fare page may be scraped. This is the project's central\n"
            "empirical finding, not a failure: a production Pushpak must be built on\n"
            "data-sharing agreements. See config.OFFICIAL_DATA_CHANNELS.\n"
        )
    print(f"Snapshot written to {out}\n")
    return 0


def cmd_discover(args) -> int:
    """Let Brave Search choose the sources, then gate every one it names.

    The point is adversarial: the curated basket refuses, and this shows the
    open web refuses too, on a list nobody here assembled.
    """
    from .collect import brave
    from .collect.discover import discover, write_snapshot

    if not brave.is_configured():
        print(
            f"{config.BRAVE_API_KEY_ENV} is not set, so there is nothing to "
            "discover.\n\n"
            "  export BRAVE_SEARCH_API_KEY=...    "
            "(free key: https://api-dashboard.search.brave.com/)\n\n"
            "The curated audit does not need it: python3 -m pushpak.cli robots-audit"
        )
        return 1

    snap = discover(include_known=args.include_known)
    write_snapshot(snap)

    print(f"\nsource discovery  ({len(snap['queries'])} queries via Brave Search)\n")
    for q in snap.get("query_log", []):
        mark = "cached" if q["cached"] else "fetched"
        detail = q["error"] or f"{q['n_results']} results"
        print(f"  {mark:<8}{q['query'][:52]:<54}{detail}")

    rows = snap["results"]
    if not rows:
        print("\nNo new hosts. Every fare page returned was already configured.")
        return 0

    print(f"\n{'host':<28}{'verdict':<10}{'rule / reason'}")
    print("-" * 96)
    for r in rows:
        verdict = "ALLOW" if r["allowed"] else "REFUSE"
        print(f"{r['source'][:26]:<28}{verdict:<10}{(r['rule'] or r['reason'])[:56]}")
    print("-" * 96)
    print(
        f"{snap['n_permitted']}/{snap['n_sources']} newly discovered fare pages "
        "permitted for automated collection."
    )
    print(f"\nwrote {config.DATA_DIR / 'robots_discovery_snapshot.json'}")
    print(
        "Brave supplied the addresses. It cannot supply fares: the index still "
        "comes from the licensed API."
    )
    return 0


def cmd_collect(args) -> int:
    quotes, summary = run_collection(
        mode=args.mode,
        observation_date=_parse_date(args.date),
        days=args.days,
        dry_run=args.dry_run,
    )
    d = summary.as_dict()
    print(json.dumps(d, indent=2))
    if d["synthetic_quotes"]:
        print(
            f"\n  NOTE: {d['synthetic_quotes']:,} of {d['total_quotes']:,} quotes "
            f"({d['synthetic_share']:.0%}) are SYNTHETIC and labelled as such."
        )
    return 0


def cmd_seed(args) -> int:
    """Backfill a synthetic history so the index has a series on day one."""
    routes = list(load_route_weights()["route_id"])
    end = _parse_date(args.date)
    storage.init_db()
    quotes = list(generate_backfill(end, args.days, routes, seed=args.seed))
    n = storage.upsert_quotes(quotes)
    print(f"Seeded {n:,} SYNTHETIC quotes over {args.days} days "
          f"({(end - timedelta(days=args.days - 1)).isoformat()} .. {end.isoformat()})")
    print("All rows carry collection_mode=SYNTHETIC.")
    return 0


def _load_and_compute():
    df = storage.load_quotes()
    if not len(df):
        print("No quotes in the database. Run:  python3 -m pushpak.cli seed --days 30",
              file=sys.stderr)
        return None
    return compute_index(df)


def cmd_index(args) -> int:
    res = _load_and_compute()
    if res is None:
        return 1
    storage.save_index(res.daily, "daily")
    storage.save_route_index(res.route_daily)
    for freq in ("weekly", "monthly"):
        storage.save_index(res.resample(freq), freq)

    d = res.daily
    print(f"\nPushpak Index base = {config.BASE_INDEX_VALUE:.0f} over {res.base_dates}\n")
    print(d[["period", "index_value", "n_quotes", "n_cells", "n_cells_imputed",
             "synthetic_share", "soldout_share", "quality_flag"]]
          .tail(args.tail).to_string(index=False))
    print(f"\nlatest: {d['index_value'].iloc[-1]:.2f}   "
          f"({len(d)} days, {d['n_quotes'].sum():,} quotes)")
    print(f"stored daily/weekly/monthly in {config.DB_PATH}")
    return 0


def cmd_explain(args) -> int:
    res = _load_and_compute()
    if res is None:
        return 1
    period = args.date or res.daily["period"].iloc[-1]
    e = explain(res, period)

    print(f"\nPushpak Index reconstruction for {e['period']}")
    print(f"  {e['formula']}")
    print(f"  base period: {', '.join(e['base_dates'])}  (index = {e['base_index_value']:.0f})\n")
    print(f"{'cell':<22}{'base':>9}{'price':>9}{'rel':>9}{'weight':>10}{'contrib':>10}  flags")
    print("-" * 88)
    for t in e["terms"]:
        flags = []
        if t["is_imputed"]:
            flags.append(f"IMPUTED:{t['imputation_method']}")
        if t["synthetic_share"] > 0:
            flags.append(f"SYN:{t['synthetic_share']:.0%}")
        if t["n_soldout"]:
            flags.append(f"soldout:{t['n_soldout']}")
        print(f"{t['cell']:<22}{t['base_price']:>9.0f}{t['price']:>9.0f}"
              f"{t['relative']:>9.4f}{t['weight']:>10.4f}{t['contribution']:>10.4f}  "
              f"{','.join(flags)}")
    print("-" * 88)
    print(f"{'SUM':<22}{'':>9}{'':>9}{'':>9}{'1.0000':>10}{e['sum_of_contributions']:>10.4f}")
    print(f"{'PUBLISHED':<22}{'':>9}{'':>9}{'':>9}{'':>10}{e['published_index_value']:>10.4f}")
    print(f"\nreconciles exactly: {e['reconciles']}  (residual {e['residual']:.2e})")
    print(f"weights: route={e['weight_provenance']['route_weights']}, "
          f"AP={e['weight_provenance']['ap_window_weights']}\n")
    return 0


def cmd_validate(args) -> int:
    res = _load_and_compute()
    if res is None:
        return 1
    bt = backtest(res)
    print(f"\nBack-test vs benchmark   verdict: {bt['verdict']}")
    print(f"benchmark official: {bt['benchmark_is_official']}  "
          f"provenance: {', '.join(bt['benchmark_provenance'])}\n")
    for k, v in bt["metrics"].items():
        print(f"  {k:<38} {v}")
    print(f"\n{bt['caveat']}\n")
    return 0


def cmd_inventory(args) -> int:
    print(json.dumps(storage.stats(), indent=2))
    return 0


def cmd_scrape(args) -> int:
    """Live scraping only. Prints exactly what each source returned."""
    quotes, summary = run_collection(
        mode="live", observation_date=_parse_date(args.date),
        days=args.days, dry_run=args.dry_run,
    )
    d = summary.as_dict()
    print(json.dumps(d, indent=2))
    print(
        f"\nlive quotes: {d['live_quotes']:,}  |  priced: {d['live_priced']:,}  |  "
        f"robots-blocked: {d['blocked_by_robots']:,}  |  "
        f"fetch failed: {d['fetch_failed']:,}  |  parse failed: {d['parse_failed']:,}"
    )
    if d["live_priced"] == 0:
        print(
            "\nNo priced quotes. Expected: every configured fare page is "
            "robots-disallowed or blocks non-browser clients.\n"
            "Run `python3 -m pushpak.cli robots-audit` for the per-source verdict."
        )
    return 0


def cmd_stats(args) -> int:
    from .stats import summary as stats_summary
    res = _load_and_compute()
    if res is None:
        return 1
    s = stats_summary(res.daily, res.cells)

    inf, vol = s["inflation"], s["volatility"]
    print(f"\nPushpak Index statistics for {s['period']}\n")
    if inf.get("available"):
        print(f"  {'index':<32}{inf['latest_index']:>12.4f}")
        for k, label in (("day_on_day_pct", "day on day %"),
                         ("week_on_week_pct", "week on week %"),
                         ("since_base_pct", "since base %")):
            v = inf.get(k)
            print(f"  {label:<32}{'n/a' if v is None else format(v, '>12.4f')}")
        print(f"  {'year on year %':<32}{'unavailable':>12}   ({inf['span_days']}-day series)")
        print(f"  {'annualised % (projection)':<32}{inf['annualised_pct']:>12.4f}")
    if vol.get("available"):
        print(f"\n  {'daily stdev %':<32}{vol['daily_stdev_pct']:>12.4f}")
        print(f"  {'mean abs daily change %':<32}{vol['mean_abs_daily_change_pct']:>12.4f}")

    dif = s["diffusion"]
    if dif.get("available"):
        print(f"\n  {'diffusion index':<32}{dif['diffusion_index']:>12.2f}"
              f"   ({dif['cells_up']} up / {dif['cells_down']} down of {dif['n_cells']})")

    att = s["attribution"]
    if att.get("available"):
        print(f"\n  route contributions to a {att['total_change_points']:+.4f} point move:")
        for r in att["routes"]:
            print(f"    {r['route_id']:<12}{r['contribution_points']:>10.4f} pts")

    disp = s["dispersion"]
    if disp.get("available"):
        print(f"\n  fare dispersion by advance-purchase window ({disp['period']}):")
        print(f"    {'window':<10}{'mean':>10}{'p10':>10}{'p90':>10}{'p90/p10':>10}{'CV':>8}")
        for w in disp["by_window"]:
            print(f"    T+{w['advance_purchase_days']:<8}{w['mean_fare']:>10.0f}"
                  f"{w['p10']:>10.0f}{w['p90']:>10.0f}{w['p90_p10_ratio']:>10.3f}"
                  f"{w['coefficient_of_variation']:>8.3f}")
    print()
    return 0


def cmd_cpi(args) -> int:
    from .cpi import build_tidy_csvs, cpi_inflation, compare_to_pushpak, statewise_latest
    if args.rebuild:
        a, b = build_tidy_csvs()
        print(f"rebuilt {a} and {b}")

    cpi = cpi_inflation(args.series)
    print(f"\nOfficial CPI General Index (All Groups), {args.series}, All India")
    print(f"{'period':<10}{'index':>10}{'MoM %':>10}{'YoY %':>10}")
    print("-" * 40)
    for r in cpi.tail(args.tail).itertuples():
        mom = "n/a" if pd.isna(r.mom_pct) else f"{r.mom_pct:.2f}"
        yoy = "n/a" if pd.isna(r.yoy_pct) else f"{r.yoy_pct:.2f}"
        print(f"{r.period:<10}{r.index_value:>10.2f}{mom:>10}{yoy:>10}")

    res = _load_and_compute()
    if res is not None:
        cmp_ = compare_to_pushpak(res.daily, args.series)
        gap = cmp_["frequency_gap"]
        print(f"\nFrequency gap")
        print(f"  CPI  : {gap['cpi']['observations']} observations over "
              f"{gap['cpi']['span_days']} days  ({gap['cpi']['granularity']})")
        print(f"  Pushpak Index : {gap['pushpak']['observations']} observations over "
              f"{gap['pushpak']['span_days']} days  ({gap['pushpak']['granularity']})")
        print(f"  Pushpak Index produces ~{gap['frequency_multiple']:.0f}x the observations per year.")
        print(f"\n{cmp_['caveat']}\n")

    sw = statewise_latest("inflation_pct")
    if sw["states"]:
        top = [x for x in sw["states"] if x["combined"] is not None][:5]
        print(f"Highest state inflation, {sw['period']}:")
        for x in top:
            print(f"  {x['state']:<34}{x['combined']:>7.2f}%")
        print()
    return 0


def cmd_elementary(args) -> int:
    """[3] Dutot vs Jevons, compiled from identical inputs."""
    df = storage.load_quotes()
    if not len(df):
        print("No quotes. Run: python3 -m pushpak.cli seed --days 30", file=sys.stderr)
        return 1
    d = compute_index(df, elementary="dutot")
    j = compute_index(df, elementary="jevons")
    if not len(d.daily) or not len(j.daily):
        print("No index computed.", file=sys.stderr)
        return 1

    m = d.daily[["period", "index_value"]].rename(columns={"index_value": "dutot"}).merge(
        j.daily[["period", "index_value"]].rename(columns={"index_value": "jevons"}),
        on="period",
    )
    m["diff"] = m["jevons"] - m["dutot"]

    print("\nElementary aggregate sensitivity")
    print("  Dutot  = ratio of ARITHMETIC mean cell prices  (the headline)")
    print("  Jevons = ratio of GEOMETRIC  mean cell prices\n")
    print(f"{'period':<12}{'dutot':>11}{'jevons':>11}{'difference':>13}")
    print("-" * 47)
    for r in m.tail(args.tail).itertuples():
        print(f"{r.period:<12}{r.dutot:>11.4f}{r.jevons:>11.4f}{r.diff:>13.4f}")
    print("-" * 47)
    print(f"max |difference| over {len(m)} days: "
          f"{m['diff'].abs().max():.4f} index points")
    print("A small spread means the headline is robust to this choice.\n")
    return 0


def cmd_adjust(args) -> int:
    """[3] Chain-linking drift and day-of-week seasonal adjustment."""
    from .adjust import chain_report, seasonal_summary
    res = _load_and_compute()
    if res is None:
        return 1

    rep = chain_report(res)
    print("\nChain-linking: the headline is FIXED-BASE and unchained.\n")
    print(f"{'link freq':<10}{'form':<12}{'links':>7}{'final':>11}"
          f"{'drift':>11}{'per-link':>11}")
    print("-" * 62)
    for v in rep["variants"]:
        if not v.get("available"):
            print(f"{v['link_frequency']:<10}{'-':<12}{'-':>7}  {v.get('reason','')}")
            continue
        print(f"{v['link_frequency']:<10}{v['link_form']:<12}{v['n_links']:>7}"
              f"{v['chained_final']:>11.3f}{v['final_drift_index_points']:>+11.3f}"
              f"{v['mean_link_excess_per_period_pct']:>+10.4f}%")
    print("-" * 62)
    if rep["least_drift"]:
        ld = rep["least_drift"]
        print(f"least drift: {ld['link_frequency']} / {ld['link_form']} "
              f"({ld['final_drift_index_points']:+.3f} points)")
    print(f"\n{rep['note']}\n")

    ss = seasonal_summary(res.daily)
    est = ss["estimation"]
    if not est.get("available"):
        print(f"Day-of-week adjustment unavailable: {est.get('reason')}\n")
        return 0
    print("Day-of-week seasonal factors "
          "(ratio to centred 7-day moving average)\n")
    print(f"{'day':<6}{'factor':>10}{'obs':>7}")
    print("-" * 23)
    for day, f in est["factors"].items():
        print(f"{day:<6}{f:>10.4f}{est['observations_per_weekday'][day]:>7}")
    print("-" * 23)
    vr = ss.get("volatility_reduction", {})
    if vr.get("stdev_raw") is not None:
        print(f"stdev raw {vr['stdev_raw']:.4f} -> "
              f"seasonally adjusted {vr['stdev_seasonally_adjusted']:.4f}")
    print(f"\n{ss['annual_seasonality']}\n")
    return 0


def cmd_alerts(args) -> int:
    """[1] Operational alerts raised by the scheduler."""
    from . import alerts as alerts_mod
    s = alerts_mod.summary()
    rows = alerts_mod.recent(limit=args.limit, severity=args.severity)

    print(f"\nAlerts   INFO {s['counts']['INFO']}  "
          f"WARNING {s['counts']['WARNING']}  CRITICAL {s['counts']['CRITICAL']}")
    print(f"webhook configured ({alerts_mod.WEBHOOK_ENV}): "
          f"{s['webhook_configured']}\n")
    if not rows:
        print("No alerts recorded. Nothing has needed an operator's attention.\n")
        return 0
    print(f"{'ts':<22}{'severity':<10}{'source':<28}title")
    print("-" * 100)
    for r in rows:
        print(f"{r['ts']:<22}{r['severity']:<10}{r['source']:<28}{r['title'][:38]}")
    print()
    return 0


def cmd_schedule(args) -> int:
    from .scheduler import Scheduler
    sch = Scheduler(tick_seconds=args.tick, mode=args.mode)
    if args.once:
        results = sch.run_due(force=args.force)
        print(json.dumps(results, indent=2, default=str))
        return 0
    sch.serve_forever()
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    uvicorn.run("pushpak.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pushpak", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("robots-audit", help="check robots.txt for every source")
    sp.set_defaults(func=cmd_robots_audit)

    sp = sub.add_parser(
        "discover",
        help="let Brave Search pick fare sources, then gate every one",
    )
    sp.add_argument("--include-known", action="store_true",
                    help="also report hosts already in config.SOURCES")
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser("collect", help="collect fare quotes")
    sp.add_argument("--mode", choices=("live", "api", "synthetic", "hybrid"),
                    default="hybrid")
    sp.add_argument("--date", help="observation date YYYY-MM-DD (default: today)")
    sp.add_argument("--days", type=int, default=1)
    sp.add_argument("--dry-run", action="store_true",
                    help="check robots + log intent, issue no requests")
    sp.set_defaults(func=cmd_collect)

    sp = sub.add_parser("seed", help="backfill labelled synthetic history")
    sp.add_argument("--days", type=int, default=30)
    sp.add_argument("--date", help="end date YYYY-MM-DD (default: today)")
    sp.add_argument("--seed", type=int, default=20260826)
    sp.set_defaults(func=cmd_seed)

    sp = sub.add_parser("index", help="compute and store the index")
    sp.add_argument("--tail", type=int, default=15)
    sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("explain", help="reconstruct one index number term by term")
    sp.add_argument("--date", help="YYYY-MM-DD (default: latest)")
    sp.set_defaults(func=cmd_explain)

    sp = sub.add_parser("validate", help="back-test against the benchmark")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("inventory", help="data inventory")
    sp.set_defaults(func=cmd_inventory)

    sp = sub.add_parser("scrape", help="[1] live scraping only, no synthetic fill")
    sp.add_argument("--date", help="observation date YYYY-MM-DD")
    sp.add_argument("--days", type=int, default=1)
    sp.add_argument("--dry-run", action="store_true",
                    help="check robots and log intent, issue no requests")
    sp.set_defaults(func=cmd_scrape)

    sp = sub.add_parser("stats", help="[3] derived statistics on the index")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("cpi", help="[6] official CPI context and the frequency gap")
    sp.add_argument("--series", choices=("combined", "rural", "urban"), default="combined")
    sp.add_argument("--tail", type=int, default=12)
    sp.add_argument("--rebuild", action="store_true",
                    help="re-parse the MoSPI workbook into tidy CSVs")
    sp.set_defaults(func=cmd_cpi)

    sp = sub.add_parser("elementary",
                        help="[3] compare the Dutot and Jevons aggregates")
    sp.add_argument("--tail", type=int, default=10)
    sp.set_defaults(func=cmd_elementary)

    sp = sub.add_parser("adjust",
                        help="[3] chain-link drift and day-of-week adjustment")
    sp.set_defaults(func=cmd_adjust)

    sp = sub.add_parser("alerts", help="[1] operational alerts")
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--severity", choices=("INFO", "WARNING", "CRITICAL"))
    sp.set_defaults(func=cmd_alerts)

    sp = sub.add_parser("schedule", help="[1] run collection and indexing on a schedule")
    sp.add_argument("--once", action="store_true", help="run due jobs and exit")
    sp.add_argument("--force", action="store_true", help="with --once, ignore due times")
    sp.add_argument("--tick", type=int, default=30, help="seconds between checks")
    sp.add_argument("--mode", choices=("live", "api", "synthetic", "hybrid"),
                    default="hybrid")
    sp.set_defaults(func=cmd_schedule)

    sp = sub.add_parser("serve", help="run the dashboard + API")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)
    sp.add_argument("--reload", action="store_true")
    sp.set_defaults(func=cmd_serve)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
