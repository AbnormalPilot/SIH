"""SQLite persistence. One file, zero setup, fully inspectable with `sqlite3`.

Chosen over Postgres deliberately: a hackathon reviewer can open data/pushpak.db
and audit every row without provisioning anything, which is exactly the
"auditable" property the brief asks for.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

import pandas as pd

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
    base_fare             REAL,
    taxes_fees            REAL,
    total_fare            REAL,
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
    crawl_delay_s     REAL,
    waited_s          REAL,
    http_status       INTEGER,
    response_bytes    INTEGER,
    latency_ms        REAL,
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
    index_value       REAL NOT NULL,
    n_quotes          INTEGER NOT NULL,
    n_cells           INTEGER NOT NULL,
    n_cells_imputed   INTEGER NOT NULL,
    synthetic_share   REAL NOT NULL,
    imputed_share     REAL NOT NULL,
    soldout_share     REAL NOT NULL,
    quality_flag      TEXT NOT NULL,      -- green | amber | red
    detail_json       TEXT,
    computed_at       TEXT NOT NULL,
    PRIMARY KEY (period, frequency)
);

CREATE TABLE IF NOT EXISTS route_index_values (
    period       TEXT NOT NULL,
    frequency    TEXT NOT NULL,
    route_id     TEXT NOT NULL,
    index_value  REAL NOT NULL,
    mean_fare    REAL,
    weight       REAL NOT NULL,
    n_quotes     INTEGER NOT NULL,
    imputed_share REAL NOT NULL,
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
def connect(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Optional[Path] = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)


def upsert_quotes(quotes: Iterable[FareQuote], db_path: Optional[Path] = None) -> int:
    rows = []
    for q in quotes:
        errs = q.validate()
        if errs:
            raise ValueError(f"invalid FareQuote {q.quote_id}: {errs}")
        r = q.to_row()
        rows.append(tuple(r[c] for c in COLUMNS))
    if not rows:
        return 0
    placeholders = ",".join("?" * len(COLUMNS))
    sql = (
        f"INSERT INTO fare_quotes ({','.join(COLUMNS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(quote_id) DO UPDATE SET "
        + ",".join(f"{c}=excluded.{c}" for c in COLUMNS if c != "quote_id")
    )
    with connect(db_path) as conn:
        conn.executemany(sql, rows)
    return len(rows)


def write_audit(records: Iterable[dict[str, Any]], db_path: Optional[Path] = None) -> int:
    records = list(records)
    if not records:
        return 0
    cols = list(records[0].keys())
    sql = (
        f"INSERT OR REPLACE INTO scrape_audit ({','.join(cols)}) "
        f"VALUES ({','.join('?' * len(cols))})"
    )
    with connect(db_path) as conn:
        conn.executemany(sql, [tuple(r[c] for c in cols) for r in records])
    return len(records)


def load_quotes(
    db_path: Optional[Path] = None,
    route_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    collection_mode: Optional[str] = None,
) -> pd.DataFrame:
    sql = "SELECT * FROM fare_quotes WHERE 1=1"
    params: list[Any] = []
    if route_id:
        sql += " AND route_id = ?"; params.append(route_id)
    if date_from:
        sql += " AND observation_date >= ?"; params.append(date_from)
    if date_to:
        sql += " AND observation_date <= ?"; params.append(date_to)
    if collection_mode:
        sql += " AND collection_mode = ?"; params.append(collection_mode)
    sql += " ORDER BY observation_date, route_id, advance_purchase_days"
    with connect(db_path) as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    if len(df):
        df["is_imputed"] = df["is_imputed"].astype(bool)
    return df


def load_audit(db_path: Optional[Path] = None, limit: int = 500) -> pd.DataFrame:
    with connect(db_path) as conn:
        return pd.read_sql_query(
            "SELECT * FROM scrape_audit ORDER BY ts DESC LIMIT ?", conn, params=[limit]
        )


def save_index(
    frame: pd.DataFrame, frequency: str, db_path: Optional[Path] = None
) -> int:
    if not len(frame):
        return 0
    cols = [
        "period", "frequency", "index_value", "n_quotes", "n_cells",
        "n_cells_imputed", "synthetic_share", "imputed_share", "soldout_share",
        "quality_flag", "detail_json", "computed_at",
    ]
    sql = (
        f"INSERT OR REPLACE INTO index_values ({','.join(cols)}) "
        f"VALUES ({','.join('?' * len(cols))})"
    )
    with connect(db_path) as conn:
        conn.executemany(sql, [tuple(r[c] for c in cols) for _, r in frame.iterrows()])
    return len(frame)


def save_route_index(frame: pd.DataFrame, db_path: Optional[Path] = None) -> int:
    if not len(frame):
        return 0
    cols = ["period", "frequency", "route_id", "index_value", "mean_fare",
            "weight", "n_quotes", "imputed_share"]
    sql = (f"INSERT OR REPLACE INTO route_index_values ({','.join(cols)}) "
           f"VALUES ({','.join('?' * len(cols))})")
    with connect(db_path) as conn:
        conn.executemany(sql, [tuple(r[c] for c in cols) for _, r in frame.iterrows()])
    return len(frame)


def load_index(
    frequency: str = "daily",
    db_path: Optional[Path] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    sql = "SELECT * FROM index_values WHERE frequency = ?"
    params: list[Any] = [frequency]
    if date_from:
        sql += " AND period >= ?"; params.append(date_from)
    if date_to:
        sql += " AND period <= ?"; params.append(date_to)
    sql += " ORDER BY period"
    with connect(db_path) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def load_route_index(
    frequency: str = "daily", db_path: Optional[Path] = None
) -> pd.DataFrame:
    with connect(db_path) as conn:
        return pd.read_sql_query(
            "SELECT * FROM route_index_values WHERE frequency = ? ORDER BY period, route_id",
            conn, params=[frequency],
        )


def start_run(run_id: str, mode: str, params: dict, db_path: Optional[Path] = None) -> None:
    from .schema import utc_now_iso
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, started_at, mode, params_json) VALUES (?,?,?,?)",
            (run_id, utc_now_iso(), mode, json.dumps(params, default=str)),
        )


def finish_run(run_id: str, summary: dict, db_path: Optional[Path] = None) -> None:
    from .schema import utc_now_iso
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE runs SET finished_at = ?, summary_json = ? WHERE run_id = ?",
            (utc_now_iso(), json.dumps(summary, default=str), run_id),
        )


def stats(db_path: Optional[Path] = None) -> dict[str, Any]:
    """Headline counts used by /api/v1/health and the dashboard banner."""
    with connect(db_path) as conn:
        def one(sql: str, *p: Any) -> Any:
            cur = conn.execute(sql, p)
            row = cur.fetchone()
            return row[0] if row else None
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
