"""Parameterized SQL queries used by the dashboard pages.

Every function returns a pandas DataFrame and is cached briefly with
`st.cache_data` so repeated page interactions stay fast. All filters
are pushed down to PostgreSQL (never filtered in Python on full tables).
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

import pandas as pd
import streamlit as st

from core import db

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _time_clause(
    start: datetime | None, end: datetime | None, column: str = "timestamp"
) -> tuple[str, dict]:
    """Build a dynamic time-range WHERE fragment with named params."""
    clauses: list[str] = []
    params: dict = {}
    if start is not None:
        clauses.append(f"{column} >= %(t_start)s")
        params["t_start"] = start
    if end is not None:
        clauses.append(f"{column} <= %(t_end)s")
        params["t_end"] = end
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _bucket_expr(bucket_seconds: int) -> str:
    return (
        "to_timestamp(floor(extract(epoch FROM timestamp) / %(bucket)s) "
        "* %(bucket)s)"
    )


def _escape_like(term: str) -> str:
    return (
        term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
@st.cache_data(ttl=db.CACHE_TTL, show_spinner=False)
def summary_stats(start: datetime | None, end: datetime | None) -> pd.DataFrame:
    """One-row summary of requests, anomalies, errors and latency."""
    where, params = _time_clause(start, end)
    sql = f"""
        SELECT count(*)                         AS total_requests,
               count(anomaly_score)             AS scored_requests,
               count(*) FILTER (WHERE is_anomaly)      AS anomaly_count,
               count(*) FILTER (WHERE status_code >= 400) AS error_count,
               avg(latency_ms)                  AS avg_latency_ms,
               percentile_disc(0.95)
                   WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
               min(timestamp)                   AS first_timestamp,
               max(timestamp)                   AS last_timestamp
        FROM logs
        {where}
    """
    return db.run_query(sql, params)


@st.cache_data(ttl=db.CACHE_TTL, show_spinner=False)
def traffic_over_time(
    start: datetime | None, end: datetime | None, bucket_seconds: int
) -> pd.DataFrame:
    """Request / error / anomaly counts per time bucket per service."""
    where, params = _time_clause(start, end)
    params["bucket"] = bucket_seconds
    sql = f"""
        SELECT {_bucket_expr(bucket_seconds)} AS bucket,
               service,
               count(*) AS requests,
               count(*) FILTER (WHERE status_code >= 400) AS errors,
               count(*) FILTER (WHERE is_anomaly) AS anomalies
        FROM logs
        {where}
        GROUP BY 1, 2
        ORDER BY 1
    """
    return db.run_query(sql, params)


@st.cache_data(ttl=db.CACHE_TTL, show_spinner=False)
def latency_over_time(
    start: datetime | None, end: datetime | None, bucket_seconds: int
) -> pd.DataFrame:
    """Average and p95 latency per time bucket (all services)."""
    where, params = _time_clause(start, end)
    params["bucket"] = bucket_seconds
    sql = f"""
        SELECT {_bucket_expr(bucket_seconds)} AS bucket,
               avg(latency_ms) AS avg_latency_ms,
               percentile_disc(0.95)
                   WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms
        FROM logs
        {where}
        GROUP BY 1
        ORDER BY 1
    """
    return db.run_query(sql, params)


# ---------------------------------------------------------------------------
# Log explorer
# ---------------------------------------------------------------------------
@st.cache_data(ttl=db.CACHE_TTL, show_spinner=False)
def filter_options() -> pd.DataFrame:
    """Distinct (service, endpoint) pairs for the filter dropdowns."""
    sql = """
        SELECT service, endpoint
        FROM logs
        WHERE endpoint IS NOT NULL
        GROUP BY 1, 2
        ORDER BY 1, 2
    """
    return db.run_query(sql)


def _log_filters(
    start: datetime | None,
    end: datetime | None,
    services: Sequence[str] | None,
    endpoints: Sequence[str] | None,
    status_classes: Sequence[str] | None,
    anomaly_filter: str,
    search: str | None,
) -> tuple[str, dict]:
    """Shared WHERE builder for the log-listing queries."""
    clauses: list[str] = []
    params: dict = {}

    time_clause, time_params = _time_clause(start, end)
    if time_clause:
        clauses.append(time_clause.lstrip(" WHERE "))
        params.update(time_params)

    if services:
        clauses.append("service = ANY(%(services)s)")
        params["services"] = list(services)
    if endpoints:
        clauses.append("endpoint = ANY(%(endpoints)s)")
        params["endpoints"] = list(endpoints)
    if status_classes:
        ranges = []
        for i, cls in enumerate(status_classes):
            low = int(cls[0]) * 100
            ranges.append(f"status_code = ANY(%(sc_{i})s)")
            params[f"sc_{i}"] = list(range(low, low + 100))
        clauses.append("(" + " OR ".join(ranges) + ")")

    if anomaly_filter == "Only anomalies":
        clauses.append("is_anomaly = TRUE")
    elif anomaly_filter == "Only normal":
        clauses.append("COALESCE(is_anomaly, FALSE) = FALSE")

    if search:
        clauses.append(
            "(message ILIKE %(search)s ESCAPE '\\' "
            "OR request_id ILIKE %(search)s ESCAPE '\\')"
        )
        params["search"] = f"%{_escape_like(search)}%"

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


@st.cache_data(ttl=db.CACHE_TTL, show_spinner=False)
def count_logs(
    start: datetime | None,
    end: datetime | None,
    services: Sequence[str] | None = None,
    endpoints: Sequence[str] | None = None,
    status_classes: Sequence[str] | None = None,
    anomaly_filter: str = "All",
    search: str | None = None,
) -> int:
    where, params = _log_filters(
        start, end, services, endpoints, status_classes, anomaly_filter, search
    )
    df = db.run_query(f"SELECT count(*) AS n FROM logs{where}", params)
    return int(df["n"].iloc[0]) if not df.empty else 0


@st.cache_data(ttl=db.CACHE_TTL, show_spinner=False)
def fetch_logs(
    start: datetime | None,
    end: datetime | None,
    services: Sequence[str] | None = None,
    endpoints: Sequence[str] | None = None,
    status_classes: Sequence[str] | None = None,
    anomaly_filter: str = "All",
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> pd.DataFrame:
    where, params = _log_filters(
        start, end, services, endpoints, status_classes, anomaly_filter, search
    )
    params["limit"] = limit
    params["offset"] = offset
    sql = f"""
        SELECT id, timestamp, service, level, endpoint, latency_ms,
               status_code, message, request_id, anomaly_score, is_anomaly
        FROM logs
        {where}
        ORDER BY timestamp DESC, id DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """
    return db.run_query(sql, params)


# ---------------------------------------------------------------------------
# Anomaly investigation
# ---------------------------------------------------------------------------
@st.cache_data(ttl=db.CACHE_TTL, show_spinner=False)
def fetch_anomalies(
    start: datetime | None,
    end: datetime | None,
    services: Sequence[str] | None = None,
    min_score: float = 0.0,
    limit: int = 500,
) -> pd.DataFrame:
    """Flagged log rows, highest anomaly score first."""
    clauses = ["is_anomaly = TRUE"]
    params: dict = {}
    time_clause, time_params = _time_clause(start, end)
    if time_clause:
        clauses.append(time_clause.lstrip(" WHERE "))
        params.update(time_params)
    if services:
        clauses.append("service = ANY(%(services)s)")
        params["services"] = list(services)
    if min_score > 0:
        clauses.append("COALESCE(anomaly_score, 0) >= %(min_score)s")
        params["min_score"] = min_score
    params["limit"] = limit
    sql = f"""
        SELECT id, timestamp, service, endpoint, latency_ms, status_code,
               message, request_id, anomaly_score
        FROM logs
        WHERE {' AND '.join(clauses)}
        ORDER BY anomaly_score DESC NULLS LAST, timestamp DESC
        LIMIT %(limit)s
    """
    return db.run_query(sql, params)


@st.cache_data(ttl=db.CACHE_TTL, show_spinner=False)
def fetch_scored_rows(
    start: datetime | None, end: datetime | None, limit: int = 20000
) -> pd.DataFrame:
    """Recently scored rows for the score-vs-time scatter plot."""
    where, params = _time_clause(start, end)
    params["limit"] = limit
    sql = f"""
        SELECT timestamp, service, endpoint, anomaly_score, is_anomaly
        FROM logs
        {where}
          {"AND" if where else "WHERE"} anomaly_score IS NOT NULL
        ORDER BY timestamp DESC
        LIMIT %(limit)s
    """
    return db.run_query(sql, params)


@st.cache_data(ttl=db.CACHE_TTL, show_spinner=False)
def fetch_flagged_rows(
    start: datetime | None, end: datetime | None, limit: int = 100000
) -> pd.DataFrame:
    """All flagged rows in a time range (used by evaluation matching)."""
    where, params = _time_clause(start, end)
    params["limit"] = limit
    sql = f"""
        SELECT timestamp, service, endpoint, anomaly_score,
               status_code, latency_ms
        FROM logs
        {where}
          {"AND" if where else "WHERE"} is_anomaly = TRUE
        ORDER BY timestamp
        LIMIT %(limit)s
    """
    return db.run_query(sql, params)


# ---------------------------------------------------------------------------
# Service health
# ---------------------------------------------------------------------------
@st.cache_data(ttl=db.CACHE_TTL, show_spinner=False)
def service_stats(start: datetime | None, end: datetime | None) -> pd.DataFrame:
    where, params = _time_clause(start, end)
    sql = f"""
        SELECT service,
               count(*) AS requests,
               count(anomaly_score) AS scored_requests,
               count(*) FILTER (WHERE status_code >= 400) AS errors,
               count(*) FILTER (WHERE is_anomaly) AS anomalies,
               avg(latency_ms) AS avg_latency_ms,
               percentile_disc(0.95)
                   WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms
        FROM logs
        {where}
        GROUP BY service
        ORDER BY service
    """
    return db.run_query(sql, params)


# ---------------------------------------------------------------------------
# Ground-truth fallback (DB table)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=db.CACHE_TTL, show_spinner=False)
def fetch_ground_truth_rows(
    start: datetime | None = None, end: datetime | None = None
) -> pd.DataFrame:
    where, params = _time_clause(start, end)
    sql = f"""
        SELECT timestamp, anomaly_type, detail
        FROM ground_truth
        {where}
        ORDER BY timestamp
    """
    return db.run_query(sql, params)
