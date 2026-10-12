"""Integration-style tests: pages render with SCHEMA-SHAPED data.

These mock `core.db.run_query` with DataFrames that exactly match the
real PostgreSQL schema (see db/init.sql and core/queries.py). No metrics
are hardcoded in the app — this only verifies that every page's
chart/table wiring works end-to-end with correctly shaped rows.
"""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

st = pytest.importorskip("streamlit.testing.v1", reason="streamlit AppTest unavailable")

FRONTEND = Path(__file__).resolve().parents[1]


def _ts(minute: int, second: int = 0) -> datetime:
    return datetime(2026, 1, 1, 12, minute, second, tzinfo=timezone.utc)


def _fake_run_query(sql: str, params=None) -> pd.DataFrame:
    """Dispatch on SQL shape to return schema-correct frames."""
    params = params or {}
    s = " ".join(sql.split()).lower()

    if "count(*) as n from logs" in s:
        return pd.DataFrame({"n": [3]})
    if "as total_requests" in s:
        return pd.DataFrame(
            {
                "total_requests": [300],
                "scored_requests": [280],
                "anomaly_count": [12],
                "error_count": [9],
                "avg_latency_ms": [42.5],
                "p95_latency_ms": [180.2],
                "first_timestamp": [_ts(0)],
                "last_timestamp": [_ts(59)],
            }
        )
    if "percentile_disc" in s and "group by 1" in s:
        # latency_over_time (per-bucket avg + p95)
        return pd.DataFrame(
            {
                "bucket": [_ts(0), _ts(1)],
                "avg_latency_ms": [40.0, 45.0],
                "p95_latency_ms": [150.0, 200.0],
            }
        )
    if "to_timestamp" in s:
        # traffic_over_time
        return pd.DataFrame(
            {
                "bucket": [_ts(0), _ts(0), _ts(1)],
                "service": ["auth-service", "payments-service", "auth-service"],
                "requests": [150, 60, 150],
                "errors": [4, 3, 5],
                "anomalies": [2, 4, 1],
            }
        )
    if "endpoint is not null" in s:
        # filter_options (distinct service/endpoint pairs)
        return pd.DataFrame(
            {
                "service": ["auth-service", "payments-service"],
                "endpoint": ["/login", "/charge"],
            }
        )
    if "from ground_truth" in s:
        return pd.DataFrame(
            {
                "timestamp": [_ts(1), _ts(3)],
                "anomaly_type": ["error_spike", "latency_spike"],
                "detail": [
                    "hammering payments-service /charge for 25s",
                    "inventory-service /check slow_mode for 20s",
                ],
            }
        )
    if "select id, timestamp, service, level" in s:
        # fetch_logs (log explorer)
        return pd.DataFrame(
            {
                "id": [1, 2, 3],
                "timestamp": [_ts(1, 5), _ts(3, 5), _ts(4)],
                "service": ["payments-service", "inventory-service", "auth-service"],
                "level": ["ERROR", "INFO", "INFO"],
                "endpoint": ["/charge", "/check", "/login"],
                "latency_ms": [900.0, 3500.0, 25.0],
                "status_code": [500, 200, 200],
                "message": ["payment gateway timeout", "in stock", "login successful"],
                "request_id": ["req-1", "req-2", "req-3"],
                "anomaly_score": [4.2, 8.1, 0.3],
                "is_anomaly": [True, True, False],
            }
        )
    if "is_anomaly = true" in s and "message" in s:
        # fetch_anomalies (investigation) — has id/message/request_id
        return pd.DataFrame(
            {
                "id": [1, 2],
                "timestamp": [_ts(1, 5), _ts(3, 5)],
                "service": ["payments-service", "inventory-service"],
                "endpoint": ["/charge", "/check"],
                "latency_ms": [900.0, 3500.0],
                "status_code": [500, 200],
                "message": ["payment gateway timeout", "in stock"],
                "request_id": ["req-1", "req-2"],
                "anomaly_score": [4.2, 8.1],
            }
        )
    if "anomaly_score is not null" in s:
        return pd.DataFrame(
            {
                "timestamp": [_ts(1, 5), _ts(3, 5)],
                "service": ["payments-service", "inventory-service"],
                "endpoint": ["/charge", "/check"],
                "anomaly_score": [4.2, 8.1],
                "is_anomaly": [True, True],
            }
        )
    if "is_anomaly = true" in s:
        # fetch_flagged_rows (evaluation) — no id/message columns
        return pd.DataFrame(
            {
                "timestamp": [_ts(1, 5), _ts(3, 5)],
                "service": ["payments-service", "inventory-service"],
                "endpoint": ["/charge", "/check"],
                "anomaly_score": [4.2, 8.1],
                "status_code": [500, 200],
                "latency_ms": [900.0, 3500.0],
            }
        )
    if "group by service" in s:
        # service_stats
        return pd.DataFrame(
            {
                "service": ["auth-service", "payments-service"],
                "requests": [200, 100],
                "scored_requests": [190, 90],
                "errors": [5, 4],
                "anomalies": [3, 9],
                "avg_latency_ms": [30.0, 80.0],
                "p95_latency_ms": [100.0, 250.0],
            }
        )
    return pd.DataFrame({"ok": [1]})


PAGES = [
    FRONTEND / "streamlit_app.py",
    FRONTEND / "pages" / "1_Log_Explorer.py",
    FRONTEND / "pages" / "2_Anomaly_Investigation.py",
    FRONTEND / "pages" / "3_Evaluation.py",
    FRONTEND / "pages" / "4_Service_Health.py",
]


@pytest.mark.parametrize("script", PAGES, ids=lambda p: p.stem)
def test_page_renders_with_schema_shaped_data(script, monkeypatch, tmp_path):
    monkeypatch.setattr("core.db.run_query", _fake_run_query)
    # Provide a ground-truth file so the Evaluation page uses the
    # file-first path exactly as documented.
    gt_file = tmp_path / "ground_truth.log"
    gt_file.write_text(
        '{"timestamp": "2026-01-01T12:01:00+00:00", "anomaly_type": "error_spike", '
        '"detail": "hammering payments-service /charge for 25s"}\n'
        '{"timestamp": "2026-01-01T12:03:00+00:00", "anomaly_type": "latency_spike", '
        '"detail": "inventory-service /check slow_mode for 20s"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("GROUND_TRUTH_LOG", str(gt_file))

    at = st.AppTest.from_file(str(script), default_timeout=120)
    at.run()

    assert not at.exception, f"{script.stem} raised:\n{at.exception}"
