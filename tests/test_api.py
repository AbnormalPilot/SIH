"""Component 8 — API contract tests.

These guard the promise a downstream consumer (NSO, RBI) relies on: every
index number arrives with its provenance attached, and nothing is described as
validated when it is not.
"""
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from pushpak import config, storage
from pushpak.collect.synthetic import generate_backfill
from pushpak.index import load_route_weights


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db = tmp_path_factory.mktemp("pushpakdb") / "test.db"
    old_db = config.DB_PATH
    config.DB_PATH = db
    storage.init_db(db)
    routes = list(load_route_weights()["route_id"])
    storage.upsert_quotes(generate_backfill(date(2026, 8, 25), 8, routes), db)

    import pushpak.api as api_mod
    api_mod._cache = {"mtime": None, "result": None}
    with TestClient(api_mod.app) as c:
        yield c
    config.DB_PATH = old_db


def test_health_reports_the_data_inventory(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert d["data"]["total_quotes"] > 0
    assert d["data"]["synthetic_quotes"] == d["data"]["total_quotes"]


def test_index_series_carries_provenance_on_every_row(client):
    d = client.get("/api/v1/index?frequency=daily").json()
    assert d["series"], "expected a non-empty series"
    for row in d["series"]:
        for field in ("synthetic_share", "imputed_share", "soldout_share",
                      "quality_flag", "n_quotes"):
            assert field in row, f"{field} missing from an index row"
        assert row["quality_flag"] in ("green", "amber", "red")


def test_all_synthetic_data_is_never_reported_green(client):
    d = client.get("/api/v1/index?frequency=daily").json()
    for row in d["series"]:
        if row["synthetic_share"] >= 0.999:
            assert row["quality_flag"] != "green"


@pytest.mark.parametrize("freq", ["daily", "weekly", "monthly"])
def test_all_frequencies_are_available(client, freq):
    d = client.get(f"/api/v1/index?frequency={freq}").json()
    assert d["frequency"] == freq and len(d["series"]) >= 1


def test_index_rejects_an_unknown_frequency(client):
    assert client.get("/api/v1/index?frequency=hourly").status_code == 422


def test_latest_index_has_a_change_and_a_base(client):
    d = client.get("/api/v1/index/latest").json()
    assert d["base_index_value"] == 100.0
    assert d["change_pct"] is not None
    assert 0.0 <= d["synthetic_share"] <= 1.0


def test_explain_endpoint_reconciles(client):
    d = client.get("/api/v1/index/explain").json()
    assert d["reconciles"] is True
    assert abs(sum(t["contribution"] for t in d["terms"])
               - d["published_index_value"]) < 0.01


def test_methodology_declares_weight_provenance(client):
    d = client.get("/api/v1/methodology").json()
    assert d["route_weights"]["provenance"] == "DGCA_DERIVED"
    assert d["advance_purchase_weights"]["provenance"] == "ASSUMED_PRIOR"
    assert "warning" in d["advance_purchase_weights"]
    assert "chain-linking" in d["not_implemented"]


def test_provenance_endpoint_reports_every_collection_mode(client):
    """The labelling contract: each mode's share is reported, whatever it is."""
    d = client.get("/api/v1/provenance").json()
    assert d["synthetic_share"] == 1.0
    assert d["live_share"] == 0.0
    assert d["official_api_share"] == 0.0
    # every mode the schema can emit must be named and explained
    from pushpak.schema import CollectionMode
    assert set(d["collection_modes"]) == {m.value for m in CollectionMode}
    assert "synthetic_share" in d["warning"]
    assert d["pii_collected"] is False


def test_shares_sum_to_one(client):
    d = client.get("/api/v1/provenance").json()
    total = d["synthetic_share"] + d["live_share"] + d["official_api_share"]
    assert abs(total - 1.0) < 1e-9, "every quote must be attributed to a mode"


def test_sources_endpoint_lists_robots_verdicts(client):
    d = client.get("/api/v1/sources").json()
    assert d["n_sources"] == len(config.SOURCES)
    assert 0 <= d["n_permitted"] <= d["n_sources"]
    # permitted must not be confused with "yields fares"
    assert "does not mean" in d["note"]
    assert "licensed_api" in d


def test_collection_job_lifecycle(client, monkeypatch):
    """Exercise the job plumbing without performing a real collection.

    TestClient runs BackgroundTasks inline, so an unpatched call here would hit
    the network and write hundreds of rows into the shared fixture database,
    breaking every later test that counts quotes.
    """
    import pushpak.api as api_mod
    from pushpak.collect.runner import RunSummary

    calls = {}

    def fake_run(job_id, mode, days, dry_run):
        calls.update(job_id=job_id, mode=mode, days=days, dry_run=dry_run)
        summary = RunSummary(run_id="test", mode=mode)
        summary.total_quotes = 0
        with api_mod._job_lock:
            api_mod._jobs[job_id].update(
                status="done", finished_at="2026-08-26T00:00:00",
                summary=summary.as_dict(),
            )

    monkeypatch.setattr(api_mod, "_run_collection_job", fake_run)

    start = client.post("/api/v1/collect?mode=live&dry_run=true").json()
    assert "job_id" in start
    assert calls["mode"] == "live" and calls["dry_run"] is True

    job = client.get(f"/api/v1/collect/{start['job_id']}").json()
    assert job["status"] == "done"
    assert job["summary"]["total_quotes"] == 0

    listing = client.get("/api/v1/collect").json()
    assert any(j["job_id"] == start["job_id"] for j in listing["jobs"])


def test_collection_rejects_an_unknown_mode(client):
    assert client.post("/api/v1/collect?mode=telepathy").status_code == 422


def test_unknown_job_is_a_404(client):
    assert client.get("/api/v1/collect/deadbeef").status_code == 404


def test_validation_does_not_claim_validation_with_a_placeholder(client):
    d = client.get("/api/v1/validation").json()
    if not d["benchmark_is_official"]:
        assert d["verdict"] == "MACHINERY_ONLY"
        assert "NOT A VALIDATION" in d["caveat"]


def test_routes_expose_dgca_coverage(client):
    d = client.get("/api/v1/routes").json()
    assert d["basket_size"] == config.BASKET_SIZE
    assert 0 < d["national_coverage_share"] < 1
    assert abs(sum(r["weight"] for r in d["routes"]) - 1.0) < 1e-6


def test_leadtime_returns_a_negative_elasticity(client):
    route = client.get("/api/v1/routes").json()["routes"][0]["route_id"]
    d = client.get(f"/api/v1/leadtime/{route}").json()
    assert len(d["curve"]) == len(config.ADVANCE_PURCHASE_WINDOWS)
    assert d["lead_time_elasticity"] < 0, "fares must rise as departure nears"


def test_unknown_route_is_a_404(client):
    assert client.get("/api/v1/routes/XXX-YYY/series").status_code == 404
    assert client.get("/api/v1/leadtime/XXX-YYY").status_code == 404


@pytest.mark.parametrize("metric", ["index", "fare", "volatility"])
def test_heatmap_metrics(client, metric):
    d = client.get(f"/api/v1/heatmap?metric={metric}").json()
    assert d["metric"] == metric
    assert len(d["values"]) == len(d["routes"])
    assert all(len(row) == len(d["periods"]) for row in d["values"])


def test_fares_endpoint_retains_unpriced_rows(client):
    d = client.get("/api/v1/fares?limit=2000").json()
    statuses = {q["quote_status"] for q in d["quotes"]}
    assert "SOLD_OUT" in statuses, "sold-out rows must be retained, not dropped"
    for q in d["quotes"]:
        if q["quote_status"] != "OK":
            assert q["total_fare"] is None


def test_fares_can_be_filtered_by_collection_mode(client):
    """Filtering must partition the table, not merely subset it."""
    total = client.get("/api/v1/fares?limit=1").json()["n_matching"]
    counts = {
        mode: client.get(f"/api/v1/fares?collection_mode={mode}&limit=1").json()["n_matching"]
        for mode in ("LIVE_SCRAPE", "OFFICIAL_API", "SYNTHETIC")
    }
    assert counts["SYNTHETIC"] > 0
    assert sum(counts.values()) == total


def test_every_collection_mode_is_filterable(client):
    """Adding a mode to the enum must not leave it unfilterable."""
    from pushpak.schema import CollectionMode
    for mode in CollectionMode:
        r = client.get(f"/api/v1/fares?collection_mode={mode.value}&limit=1")
        assert r.status_code == 200, f"{mode.value} is not an accepted filter"


def test_audit_endpoint_states_the_policy(client):
    d = client.get("/api/v1/audit").json()
    assert d["policy"]["respect_robots"] is True
    assert d["policy"]["fail_closed"] is True
    assert d["policy"]["pii_collected"] is False


def test_dashboard_and_docs_render(client):
    assert client.get("/dashboard").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_static_handler_rejects_path_traversal(client):
    r = client.get("/static/../../../etc/passwd")
    assert r.status_code in (404, 400)
