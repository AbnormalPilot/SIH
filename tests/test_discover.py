"""Component 8 — tests for Brave-driven source discovery.

Two things need pinning. First, discovery must be *optional*: with no API key
the whole pipeline behaves exactly as it did before, because a reviewer on a
laptop with no key still has to be able to run everything. Second, Brave must
never be able to reach the index — it supplies addresses to audit, and the
gating of those addresses is the same fail-closed gate the curated sources go
through.

No test here touches the network. The Brave client's single fetch method is
replaced, and the robots gate is replaced with a recording stub.
"""
import json

import pytest

from pushpak import config
from pushpak.collect import brave, discover as disc
from pushpak.collect.brave import BraveClient, BraveResult, BraveUnconfigured
from pushpak.collect.robots import RobotsVerdict


# ---------------------------------------------------------------------------
# Host extraction and filtering
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url,expected", [
    ("https://www.goindigo.in/booking/flight-select.html", "goindigo.in"),
    ("https://skyscanner.co.in/transport/flights/del/bom/", "skyscanner.co.in"),
    ("http://WWW.Cleartrip.COM:443/flights/search?x=1", "cleartrip.com"),
    ("not a url at all", None),
    ("", None),
])
def test_registrable_host(url, expected):
    assert disc.registrable_host(url) == expected


def test_excluded_domains_cover_subdomains():
    assert disc.is_excluded("wikipedia.org")
    assert disc.is_excluded("en.wikipedia.org")
    assert not disc.is_excluded("notwikipedia.org")


def test_fare_page_heuristic_keeps_homepages_and_drops_blog_posts():
    # A bare homepage is worth auditing: its robots.txt governs everything.
    assert disc.looks_like_fare_page("https://example.in/")
    assert disc.looks_like_fare_page("https://example.in/flights/search")
    assert not disc.looks_like_fare_page("https://example.in/blog/2026/tips")


def test_candidates_keep_the_highest_ranked_url_per_host():
    results = [
        BraveResult("A", "https://www.ex.in/flights/search", "d", "q1"),
        BraveResult("B", "https://ex.in/flights/other", "d", "q2"),
        BraveResult("C", "https://en.wikipedia.org/wiki/Airfare", "d", "q1"),
    ]
    cands = disc.candidates(results)
    assert [c["source"] for c in cands] == ["ex.in"]
    # The first result for the host wins, and both queries are credited.
    assert cands[0]["url"] == "https://www.ex.in/flights/search"
    assert cands[0]["found_by"] == ["q1", "q2"]


# ---------------------------------------------------------------------------
# Optionality
# ---------------------------------------------------------------------------
def test_discovery_is_a_no_op_without_a_key(monkeypatch):
    monkeypatch.delenv(config.BRAVE_API_KEY_ENV, raising=False)
    snap = disc.discover()
    assert snap["configured"] is False
    assert snap["n_sources"] == 0
    assert snap["results"] == []
    assert config.BRAVE_API_KEY_ENV in snap["note"]


def test_client_refuses_to_search_without_a_key(monkeypatch):
    monkeypatch.delenv(config.BRAVE_API_KEY_ENV, raising=False)
    with pytest.raises(BraveUnconfigured):
        BraveClient().search("anything")


def test_sources_endpoint_reports_discovery_unconfigured(monkeypatch):
    monkeypatch.delenv(config.BRAVE_API_KEY_ENV, raising=False)
    from fastapi.testclient import TestClient
    from pushpak.api import app

    body = TestClient(app).get("/api/v1/sources").json()
    assert body["discovery"]["configured"] is False
    assert body["discovery"]["env_var"] == config.BRAVE_API_KEY_ENV


# ---------------------------------------------------------------------------
# The gate still decides
# ---------------------------------------------------------------------------
class _StubGate:
    """Records what it was asked, and refuses everything except one host."""

    def __init__(self, allow_host="permissive.in"):
        self.allow_host = allow_host
        self.checked: list[str] = []

    def check(self, url):
        self.checked.append(url)
        host = disc.registrable_host(url)
        allowed = host == self.allow_host
        return RobotsVerdict(
            url=url, host=host, allowed=allowed,
            reason="ALLOWED" if allowed else "DISALLOWED",
            rule="Disallow: /" if not allowed else None,
        )


def _client_returning(payload_by_query):
    client = BraveClient(key="test-key", cache_dir=None, ttl=0)
    client.cache_dir = None  # replaced below; no disk access in tests

    def fake_fetch(query, count):
        return payload_by_query.get(query, {"web": {"results": []}})

    client._fetch = fake_fetch
    client._read_cache = lambda path: None
    client._write_cache = lambda path, payload: None
    client._cache_path = lambda query, count: None
    return client


def test_every_discovered_host_goes_through_the_robots_gate():
    payload = {"web": {"results": [
        {"title": "Cheap flights", "url": "https://www.permissive.in/flights",
         "description": "book"},
        {"title": "Also flights", "url": "https://www.refuses.in/flights/search",
         "description": "book"},
    ]}}
    client = _client_returning({"q": payload})
    gate = _StubGate()

    snap = disc.discover(queries=["q"], client=client, gate=gate)

    assert sorted(gate.checked) == [
        "https://www.permissive.in/flights",
        "https://www.refuses.in/flights/search",
    ]
    assert snap["n_sources"] == 2
    assert snap["n_permitted"] == 1
    assert {r["source"] for r in snap["results"]} == {"permissive.in", "refuses.in"}


def test_discovery_never_yields_a_price_field():
    """Brave supplies addresses. Nothing it returns may look like a fare."""
    payload = {"web": {"results": [
        {"title": "Delhi to Mumbai from Rs 4,299",
         "url": "https://www.permissive.in/flights",
         "description": "Fares from Rs 4,299 one way"},
    ]}}
    snap = disc.discover(
        queries=["q"], client=_client_returning({"q": payload}), gate=_StubGate()
    )
    row = snap["results"][0]
    forbidden = {"price", "fare", "fare_inr", "amount", "currency"}
    assert forbidden.isdisjoint(row)
    # The snippet is kept verbatim as evidence, but it is a string in a named
    # snippet field, not a number anything downstream could mistake for a fare.
    assert isinstance(row["snippet"], str)


def test_configured_sources_are_not_reported_as_discoveries():
    known = config.SOURCES[0].name
    payload = {"web": {"results": [
        {"title": "known", "url": f"https://www.{known}/flights", "description": ""},
        {"title": "new", "url": "https://www.permissive.in/flights", "description": ""},
    ]}}
    client = _client_returning({"q": payload})

    snap = disc.discover(queries=["q"], client=client, gate=_StubGate())
    assert {r["source"] for r in snap["results"]} == {"permissive.in"}

    client = _client_returning({"q": payload})
    snap = disc.discover(
        queries=["q"], client=client, gate=_StubGate(), include_known=True
    )
    by_host = {r["source"]: r for r in snap["results"]}
    assert by_host[known]["already_configured"] is True
    assert by_host["permissive.in"]["already_configured"] is False


def test_a_failed_query_is_logged_and_does_not_abort_the_run():
    client = BraveClient(key="test-key")
    client._read_cache = lambda path: None
    client._write_cache = lambda path, payload: None
    client._cache_path = lambda query, count: None

    def fake_fetch(query, count):
        if query == "bad":
            raise RuntimeError("429 Too Many Requests")
        return {"web": {"results": [
            {"title": "t", "url": "https://www.permissive.in/flights", "description": ""},
        ]}}

    client._fetch = fake_fetch
    snap = disc.discover(queries=["bad", "good"], client=client, gate=_StubGate())

    log = {q["query"]: q for q in snap["query_log"]}
    assert "429" in log["bad"]["error"]
    assert log["good"]["error"] is None
    assert snap["n_sources"] == 1


def test_snapshot_round_trips(tmp_path):
    path = tmp_path / "snap.json"
    snap = disc.discover(
        queries=["q"],
        client=_client_returning({"q": {"web": {"results": [
            {"title": "t", "url": "https://www.permissive.in/flights", "description": ""},
        ]}}}),
        gate=_StubGate(),
    )
    disc.write_snapshot(snap, path)
    assert disc.load_snapshot(path) == json.loads(path.read_text())


def test_load_snapshot_tolerates_a_corrupt_file(tmp_path):
    path = tmp_path / "snap.json"
    path.write_text("{not json")
    assert disc.load_snapshot(path) is None
