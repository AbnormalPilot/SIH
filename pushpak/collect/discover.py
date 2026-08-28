"""Component 1 — source discovery, so the compliance finding is not hand-picked.

The curated twelve sources in ``config.SOURCES`` carry the project's central
result: nine of them refuse automated collection. The fair objection to that
result is that somebody chose the twelve. This module removes the chooser.

    Brave Search names the fare pages  ->  the same fail-closed gate runs
    against each one  ->  the verdicts land beside the curated ones.

Nothing here touches the index. Brave returns web results, not fares; a
discovered domain becomes a row in an audit ledger and never a price.

Discovery is optional in the strongest sense: with no API key the module
reports that and returns an empty result, and `pushpak.cli robots-audit`, the
API and the whole test suite behave exactly as they did before.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable, Optional
from urllib.parse import urlparse

from .. import config
from .brave import BraveClient, BraveResult, BraveUnconfigured, is_configured
from .robots import RobotsGate

SNAPSHOT_PATH = config.DATA_DIR / "robots_discovery_snapshot.json"


# ---------------------------------------------------------------------------
def registrable_host(url: str) -> Optional[str]:
    """``https://www.goindigo.in/booking/...`` -> ``goindigo.in``.

    Deliberately not a public-suffix lookup. Stripping ``www.`` is enough to
    match the naming already used by ``config.SOURCES`` ("goindigo.in",
    "skyscanner.co.in"), and a real PSL would add a dependency and a data file
    to a prototype that gains nothing from either. The cost is that
    ``fares.example.co.in`` and ``example.co.in`` audit as two hosts, which is
    harmless here: both get gated, and both verdicts are true.
    """
    try:
        netloc = urlparse(url).netloc.lower()
    except ValueError:
        return None
    if not netloc:
        return None
    netloc = netloc.split("@")[-1].split(":")[0]
    return netloc[4:] if netloc.startswith("www.") else netloc


def is_excluded(host: str) -> bool:
    """True for hosts that appear in any flight search and sell no fares."""
    return any(
        host == bad or host.endswith("." + bad)
        for bad in config.DISCOVERY_EXCLUDED_DOMAINS
    )


def looks_like_fare_page(url: str) -> bool:
    """A blog post about airfares is not a fare page. Match on the path only."""
    path = (urlparse(url).path or "/").lower()
    if path in ("", "/"):
        # A bare homepage is a legitimate thing to audit: it is where a fare
        # search starts, and its robots.txt governs everything below it.
        return True
    return any(hint in path for hint in config.DISCOVERY_PATH_HINTS)


# ---------------------------------------------------------------------------
def candidates(results: Iterable[BraveResult]) -> list[dict]:
    """Collapse search results to one auditable candidate per host.

    The first result for a host wins, so the URL that gets gated is the one the
    search engine ranked highest — not one this code went looking for.
    """
    seen: dict[str, dict] = {}
    for r in results:
        host = registrable_host(r.url)
        if not host or is_excluded(host) or not looks_like_fare_page(r.url):
            continue
        if host in seen:
            seen[host]["found_by"].append(r.query)
            continue
        seen[host] = {
            "source": host,
            "url": r.url,
            "title": r.title,
            "snippet": r.description,
            "found_by": [r.query],
        }
    return list(seen.values())


def discover(
    queries: Iterable[str] = config.DISCOVERY_QUERIES,
    client: BraveClient | None = None,
    gate: RobotsGate | None = None,
    include_known: bool = False,
) -> dict:
    """Search, gate every host found, and return a snapshot-shaped dict.

    ``include_known=False`` drops hosts already in ``config.SOURCES``: their
    verdicts come from ``robots-audit``, and reporting them twice would inflate
    the discovered count with sources nobody discovered.
    """
    queries = list(queries)
    if not is_configured() and client is None:
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "configured": False,
            "note": (
                f"{config.BRAVE_API_KEY_ENV} is not set, so no discovery ran. "
                "The curated audit in robots_compliance_snapshot.json is "
                "unaffected."
            ),
            "queries": queries,
            "n_sources": 0,
            "n_permitted": 0,
            "results": [],
        }

    client = client or BraveClient()
    gate = gate or RobotsGate()

    found: list[BraveResult] = []
    for q in queries:
        try:
            found.extend(client.search(q))
        except BraveUnconfigured:
            break

    known = {s.name for s in config.SOURCES}
    rows = []
    for cand in candidates(found):
        already = cand["source"] in known
        if already and not include_known:
            continue
        verdict = gate.check(cand["url"])
        rows.append({
            **cand,
            "already_configured": already,
            "discovered": True,
            **verdict.as_dict(),
        })

    rows.sort(key=lambda r: (r["allowed"], r["source"]))
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "configured": True,
        "user_agent": config.USER_AGENT,
        "policy": "fail-closed: unreachable robots.txt is treated as DISALLOWED",
        "note": (
            "Hosts named by Brave Search, gated by the same robots.txt parser "
            "as the curated sources. Brave supplies addresses, never fares."
        ),
        "queries": queries,
        "query_log": client.log,
        "n_sources": len(rows),
        "n_permitted": sum(1 for r in rows if r["allowed"]),
        "results": rows,
    }


def write_snapshot(snapshot: dict, path=SNAPSHOT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2))


def load_snapshot(path=SNAPSHOT_PATH) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
