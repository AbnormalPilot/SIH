"""Postgres persistence. One connection string, everything auditable in SQL.

Every table is append-or-upsert keyed on a natural id, so a re-run of a
collection or an index computation is idempotent: a reviewer can replay a day
and get the same rows, not duplicates.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Optional

import pandas as pd
import psycopg
from psycopg.rows import dict_row

from . import config
from .schema import COLUMNS, FareQuote

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fare_quotes (
    quote_id              TEXT PRIMARY KEY,
    origin                TEXT NOT NULL,
    destination           TEXT NOT NULL,
    route_id              TEXT NOT NULL,
    carrier               TEXT,
    source                TEXT NOT NULL,
    source_type           TEXT NOT NULL,
    collection_mode       TEXT NOT NULL,          -- LIVE_SCRAPE | SYNTHETIC
    scrape_ts             TEXT NOT NULL,
    observation_date      TEXT NOT NULL,
    travel_date           TEXT NOT NULL,
    advance_purchase_days INTEGER NOT NULL,
    fare_class            TEXT NOT NULL,
    base_fare             DOUBLE PRECISION,
    taxes_fees            DOUBLE PRECISION,
    total_fare            DOUBLE PRECISION,
    currency              TEXT NOT NULL,
    quote_status          TEXT NOT NULL,
    is_imputed            INTEGER NOT NULL DEFAULT 0,
    imputation_method     TEXT NOT NULL DEFAULT 'NONE',
    imputed_from          TEXT,
    flight_number         TEXT,
    departure_time        TEXT,
    stops                 INTEGER,
    audit_request_id      TEXT,
    notes                 TEXT,
    schema_version        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_quotes_obs   ON fare_quotes(observation_date);
CREATE INDEX IF NOT EXISTS ix_quotes_route ON fare_quotes(route_id, observation_date);
CREATE INDEX IF NOT EXISTS ix_quotes_mode  ON fare_quotes(collection_mode);

-- Component 1: the auditability requirement. Append-only request ledger.
CREATE TABLE IF NOT EXISTS scrape_audit (
    request_id        TEXT PRIMARY KEY,
    ts                TEXT NOT NULL,
    source            TEXT NOT NULL,
    host              TEXT NOT NULL,
    url               TEXT,
    url_pattern       TEXT NOT NULL,
    method            TEXT NOT NULL,
    user_agent        TEXT NOT NULL,
    robots_checked    INTEGER NOT NULL,
    robots_allowed    INTEGER,
    robots_rule       TEXT,
    crawl_delay_s     DOUBLE PRECISION,
    waited_s          DOUBLE PRECISION,
    http_status       INTEGER,
    response_bytes    INTEGER,
    latency_ms        DOUBLE PRECISION,
    outcome           TEXT NOT NULL,
    error             TEXT,
    run_id            TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_run ON scrape_audit(run_id);
CREATE INDEX IF NOT EXISTS ix_audit_ts  ON scrape_audit(ts);

-- Component 3: computed index values, versioned by run so results reproduce.
CREATE TABLE IF NOT EXISTS index_values (
    period            TEXT NOT NULL,      -- YYYY-MM-DD | YYYY-Www | YYYY-MM
    frequency         TEXT NOT NULL,      -- daily | weekly | monthly
    index_value       DOUBLE PRECISION NOT NULL,  -- arithmetic (Dutot) elementary aggregate
    index_value_jevons DOUBLE PRECISION,          -- geometric (Jevons), same weights
    n_quotes          INTEGER NOT NULL,
    n_cells           INTEGER NOT NULL,
    n_cells_imputed   INTEGER NOT NULL,
    synthetic_share   DOUBLE PRECISION NOT NULL,
    imputed_share     DOUBLE PRECISION NOT NULL,
    soldout_share     DOUBLE PRECISION NOT NULL,
    quality_flag      TEXT NOT NULL,      -- green | amber | red
    detail_json       TEXT,
    computed_at       TEXT NOT NULL,
    PRIMARY KEY (period, frequency)
);
-- A database created before the Jevons aggregate existed keeps its old
-- index_values table under CREATE TABLE IF NOT EXISTS. Add the column rather
-- than making a reviewer drop their data to read this build.
ALTER TABLE index_values
    ADD COLUMN IF NOT EXISTS index_value_jevons DOUBLE PRECISION;

CREATE TABLE IF NOT EXISTS route_index_values (
    period       TEXT NOT NULL,
    frequency    TEXT NOT NULL,
    route_id     TEXT NOT NULL,
    index_value  DOUBLE PRECISION NOT NULL,
    mean_fare    DOUBLE PRECISION,
    weight       DOUBLE PRECISION NOT NULL,
    n_quotes     INTEGER NOT NULL,
    imputed_share DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (period, frequency, route_id)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    mode        TEXT NOT NULL,
    params_json TEXT,
    finished_at TEXT,
    summary_json TEXT
);
"""


@contextmanager
def connect(db_url: Optional[str] = None) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(str(db_url or config.DB_URL), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _upsert_sql(table: str, cols: list[str], keys: list[str]) -> str:
    """INSERT ... ON CONFLICT DO UPDATE over every non-key column."""
    updates = ",".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in keys)
    return (
        f"INSERT INTO {table} ({','.join(cols)}) "
        f"VALUES ({','.join(['%s'] * len(cols))}) "
        f"ON CONFLICT ({','.join(keys)}) DO UPDATE SET {updates}"
    )


def _vals(row: Any, cols: Iterable[str]) -> tuple:
    """Row values as plain Python scalars.

    pandas hands out numpy scalars, and psycopg has no adapter for numpy.int64.
    `.item()` unwraps them; anything without it (str, None) passes through.
    """
    out = []
    for c in cols:
        v = row[c]
        out.append(v.item() if hasattr(v, "item") else v)
    return tuple(out)


def _frame(conn: psycopg.Connection, sql: str, params: Any = ()) -> pd.DataFrame:
    """Query -> DataFrame, keeping the column names even on an empty result."""
    cur = conn.execute(sql, params)
    cols = [d.name for d in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=cols)


def init_db(db_url: Optional[str] = None) -> None:
    with connect(db_url) as conn:
        conn.execute(SCHEMA_SQL)


def upsert_quotes(quotes: Iterable[FareQuote], db_url: Optional[str] = None) -> int:
    rows = []
    for q in quotes:
        errs = q.validate()
        if errs:
            raise ValueError(f"invalid FareQuote {q.quote_id}: {errs}")
        r = q.to_row()
        rows.append(_vals(r, COLUMNS))
    if not rows:
        return 0
    sql = _upsert_sql("fare_quotes", list(COLUMNS), ["quote_id"])
    with connect(db_url) as conn:
        conn.cursor().executemany(sql, rows)
    return len(rows)


def write_audit(records: Iterable[dict[str, Any]], db_url: Optional[str] = None) -> int:
    records = list(records)
    if not records:
        return 0
    cols = list(records[0].keys())
    sql = _upsert_sql("scrape_audit", cols, ["request_id"])
    with connect(db_url) as conn:
        conn.cursor().executemany(sql, [_vals(r, cols) for r in records])
    return len(records)


def load_quotes(
    db_url: Optional[str] = None,
    route_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    collection_mode: Optional[str] = None,
) -> pd.DataFrame:
    sql = "SELECT * FROM fare_quotes WHERE 1=1"
    params: list[Any] = []
    if route_id:
        sql += " AND route_id = %s"; params.append(route_id)
    if date_from:
        sql += " AND observation_date >= %s"; params.append(date_from)
    if date_to:
        sql += " AND observation_date <= %s"; params.append(date_to)
    if collection_mode:
        sql += " AND collection_mode = %s"; params.append(collection_mode)
    sql += " ORDER BY observation_date, route_id, advance_purchase_days"
    with connect(db_url) as conn:
        df = _frame(conn, sql, params)
    if len(df):
        df["is_imputed"] = df["is_imputed"].astype(bool)
    return df


def load_audit(db_url: Optional[str] = None, limit: int = 500) -> pd.DataFrame:
    with connect(db_url) as conn:
        return _frame(
            conn, "SELECT * FROM scrape_audit ORDER BY ts DESC LIMIT %s", [limit]
        )


def save_index(
    frame: pd.DataFrame, frequency: str, db_url: Optional[str] = None
) -> int:
    if not len(frame):
        return 0
    cols = [
        "period", "frequency", "index_value", "index_value_jevons",
        "n_quotes", "n_cells",
        "n_cells_imputed", "synthetic_share", "imputed_share", "soldout_share",
        "quality_flag", "detail_json", "computed_at",
    ]
    sql = _upsert_sql("index_values", cols, ["period", "frequency"])
    with connect(db_url) as conn:
        conn.cursor().executemany(sql, [_vals(r, cols) for _, r in frame.iterrows()])
    return len(frame)


def save_route_index(frame: pd.DataFrame, db_url: Optional[str] = None) -> int:
    if not len(frame):
        return 0
    cols = ["period", "frequency", "route_id", "index_value", "mean_fare",
            "weight", "n_quotes", "imputed_share"]
    sql = _upsert_sql("route_index_values", cols, ["period", "frequency", "route_id"])
    with connect(db_url) as conn:
        conn.cursor().executemany(sql, [_vals(r, cols) for _, r in frame.iterrows()])
    return len(frame)


def load_index(
    frequency: str = "daily",
    db_url: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    sql = "SELECT * FROM index_values WHERE frequency = %s"
    params: list[Any] = [frequency]
    if date_from:
        sql += " AND period >= %s"; params.append(date_from)
    if date_to:
        sql += " AND period <= %s"; params.append(date_to)
    sql += " ORDER BY period"
    with connect(db_url) as conn:
        return _frame(conn, sql, params)


def load_route_index(
    frequency: str = "daily", db_url: Optional[str] = None
) -> pd.DataFrame:
    with connect(db_url) as conn:
        return _frame(
            conn,
            "SELECT * FROM route_index_values WHERE frequency = %s ORDER BY period, route_id",
            [frequency],
        )


def start_run(run_id: str, mode: str, params: dict, db_url: Optional[str] = None) -> None:
    from .schema import utc_now_iso
    with connect(db_url) as conn:
        conn.execute(
            "INSERT INTO runs (run_id, started_at, mode, params_json) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (run_id) DO UPDATE SET started_at=EXCLUDED.started_at, "
            "mode=EXCLUDED.mode, params_json=EXCLUDED.params_json",
            (run_id, utc_now_iso(), mode, json.dumps(params, default=str)),
        )


def finish_run(run_id: str, summary: dict, db_url: Optional[str] = None) -> None:
    from .schema import utc_now_iso
    with connect(db_url) as conn:
        conn.execute(
            "UPDATE runs SET finished_at = %s, summary_json = %s WHERE run_id = %s",
            (utc_now_iso(), json.dumps(summary, default=str), run_id),
        )


def stats(db_url: Optional[str] = None) -> dict[str, Any]:
    """Headline counts used by /api/v1/health and the dashboard banner."""
    with connect(db_url) as conn:
        def one(sql: str, *p: Any) -> Any:
            row = conn.execute(sql, p).fetchone()
            return next(iter(row.values())) if row else None
        total = one("SELECT COUNT(*) FROM fare_quotes") or 0
        return {
            "total_quotes": total,
            "live_quotes": one(
                "SELECT COUNT(*) FROM fare_quotes WHERE collection_mode='LIVE_SCRAPE'") or 0,
            "synthetic_quotes": one(
                "SELECT COUNT(*) FROM fare_quotes WHERE collection_mode='SYNTHETIC'") or 0,
            "official_api_quotes": one(
                "SELECT COUNT(*) FROM fare_quotes WHERE collection_mode='OFFICIAL_API'") or 0,
            "priced_quotes": one(
                "SELECT COUNT(*) FROM fare_quotes WHERE quote_status='OK'") or 0,
            "soldout_quotes": one(
                "SELECT COUNT(*) FROM fare_quotes WHERE quote_status='SOLD_OUT'") or 0,
            "imputed_quotes": one(
                "SELECT COUNT(*) FROM fare_quotes WHERE is_imputed=1") or 0,
            "first_observation": one("SELECT MIN(observation_date) FROM fare_quotes"),
            "last_observation": one("SELECT MAX(observation_date) FROM fare_quotes"),
            "audit_requests": one("SELECT COUNT(*) FROM scrape_audit") or 0,
            "distinct_routes": one("SELECT COUNT(DISTINCT route_id) FROM fare_quotes") or 0,
            "distinct_sources": one("SELECT COUNT(DISTINCT source) FROM fare_quotes") or 0,
        }


TABLES = ("scheduled_runs", "route_index_values", "index_values",
          "scrape_audit", "fare_quotes", "runs")


def drop_all(db_url: Optional[str] = None) -> None:
    """Wipe every Pushpak table. Backs `make clean`, which used to rm a file."""
    with connect(db_url) as conn:
        for t in TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")


def db_version(db_url: Optional[str] = None) -> tuple:
    """Cheap change token for caches: (row count, newest scrape timestamp).

    Replaces the SQLite file mtime the API used to watch. Any insert or
    re-scrape moves one of the two, which is all the cache needs.
    """
    with connect(db_url) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, MAX(scrape_ts) AS ts FROM fare_quotes"
        ).fetchone()
    return (row["n"], row["ts"])
