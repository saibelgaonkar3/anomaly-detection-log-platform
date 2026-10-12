"""Tests for the pure-pandas transform helpers."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from core import transforms


def ts(minutes: int = 0, seconds: int = 0) -> pd.Timestamp:
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    return pd.Timestamp(base + timedelta(minutes=minutes, seconds=seconds))


# ---------------------------------------------------------------------------
# bucket sizing / rates
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "start,end,expected",
    [
        (ts(), ts(seconds=900), 30),
        (ts(), ts(minutes=90), 60),
        (ts(), ts(minutes=600), 300),
        (ts(), ts(minutes=3000), 3600),
        (None, ts(), 3600),
    ],
)
def test_bucket_seconds_for_range(start, end, expected):
    assert transforms.bucket_seconds_for_range(start, end) == expected


def test_rate_zero_denominator():
    assert transforms.rate(3, 0) == 0.0
    assert transforms.rate(1, 4) == 25.0


def test_anomaly_rate_uses_scored_rows():
    # 5 anomalies among 50 scored of 100 total -> 10%, not 5%.
    assert transforms.anomaly_rate(5, 50, 100) == 10.0
    # Falls back to total when nothing scored yet.
    assert transforms.anomaly_rate(0, 0, 100) == 0.0


# ---------------------------------------------------------------------------
# ground-truth helpers
# ---------------------------------------------------------------------------
def test_eligible_events_excludes_suspicious_sequence():
    gt = pd.DataFrame(
        {
            "timestamp": [ts(0), ts(1), ts(2)],
            "anomaly_type": ["error_spike", "suspicious_sequence", "latency_spike"],
            "detail": ["a", "b", "c"],
        }
    )
    out = transforms.eligible_events(gt)
    assert list(out["anomaly_type"]) == ["error_spike", "latency_spike"]


def test_event_service_extraction():
    assert transforms.event_service("hammering payments-service /charge for 25s") == "payments-service"
    assert transforms.event_service("inventory-service /check slow_mode for 20s") == "inventory-service"
    assert transforms.event_service("no service named here") is None
    assert transforms.event_service(None) is None


# ---------------------------------------------------------------------------
# clustering
# ---------------------------------------------------------------------------
def _flagged(rows):
    return pd.DataFrame(rows, columns=["timestamp", "service", "endpoint", "anomaly_score"])


def test_cluster_merges_nearby_and_splits_on_gap():
    flagged = _flagged(
        [
            (ts(seconds=0), "payments-service", "/charge", 3.1),
            (ts(seconds=10), "payments-service", "/charge", 4.2),
            (ts(seconds=20), "payments-service", "/charge", 3.5),
            # 5-minute gap -> new cluster
            (ts(minutes=5, seconds=10), "payments-service", "/charge", 3.3),
            # different service -> separate cluster regardless
            (ts(seconds=5), "inventory-service", "/check", 9.9),
        ]
    )
    out = transforms.cluster_flagged_events(flagged)
    assert len(out) == 3
    payments = out[out["service"] == "payments-service"].sort_values("start")
    assert len(payments) == 2
    first = payments.iloc[0]
    assert first["rows"] == 3
    assert first["max_score"] == pytest.approx(4.2)
    assert (first["end"] - first["start"]).total_seconds() == 20
    assert len(out[out["service"] == "inventory-service"]) == 1


def test_cluster_empty_input():
    out = transforms.cluster_flagged_events(pd.DataFrame())
    assert out.empty


# ---------------------------------------------------------------------------
# evaluation matching
# ---------------------------------------------------------------------------
def _gt(rows):
    return pd.DataFrame(rows, columns=["timestamp", "anomaly_type", "detail"])


def test_perfect_detection_gives_full_scores():
    gt = _gt([(ts(minutes=1), "error_spike", "hammering payments-service /charge for 25s")])
    det = _flagged(
        [(ts(minutes=1, seconds=5), "payments-service", "/charge", 4.0)]
    )
    clusters = transforms.cluster_flagged_events(det)
    res = transforms.evaluate_detections(gt, clusters)
    assert (res["tp"], res["fp"], res["fn"]) == (1, 0, 0)
    assert res["precision"] == pytest.approx(1.0)
    assert res["recall"] == pytest.approx(1.0)
    assert res["f1"] == pytest.approx(1.0)


def test_missed_event_counts_as_false_negative():
    gt = _gt(
        [
            (ts(minutes=1), "latency_spike", "inventory-service /check slow_mode for 20s"),
            (ts(minutes=2), "error_spike", "hammering payments-service /charge for 25s"),
        ]
    )
    det = _flagged(
        [(ts(minutes=2, seconds=5), "payments-service", "/charge", 3.7)]
    )
    clusters = transforms.cluster_flagged_events(det)
    res = transforms.evaluate_detections(gt, clusters)
    assert res["tp"] == 1
    assert res["fn"] == 1
    assert res["fp"] == 0
    assert res["recall"] == pytest.approx(0.5)


def test_unmatched_cluster_counts_as_false_positive():
    gt = _gt([(ts(minutes=1), "error_spike", "hammering payments-service /charge for 25s")])
    det = _flagged(
        [
            (ts(minutes=1, seconds=5), "payments-service", "/charge", 4.0),
            # far away in time -> unmatched
            (ts(minutes=30), "orders-service", "/create", 5.0),
        ]
    )
    clusters = transforms.cluster_flagged_events(det)
    res = transforms.evaluate_detections(gt, clusters)
    assert res["tp"] == 1
    assert res["fp"] == 1
    assert res["precision"] == pytest.approx(0.5)


def test_service_mismatch_is_not_a_match():
    # Detection in the right window but wrong service must not match.
    gt = _gt([(ts(minutes=1), "latency_spike", "inventory-service /check slow_mode for 20s")])
    det = _flagged(
        [(ts(minutes=1, seconds=5), "orders-service", "/create", 8.0)]
    )
    clusters = transforms.cluster_flagged_events(det)
    res = transforms.evaluate_detections(gt, clusters)
    assert res["tp"] == 0
    assert res["fn"] == 1
    assert res["fp"] == 1


def test_detection_before_window_still_matches_with_tolerance():
    # Scorer batch delay: flagged slightly before the injection timestamp.
    gt = _gt([(ts(minutes=1), "error_spike", "hammering payments-service /charge for 25s")])
    det = _flagged(
        [(ts(minutes=0, seconds=57), "payments-service", "/charge", 3.2)]
    )
    clusters = transforms.cluster_flagged_events(det)
    res = transforms.evaluate_detections(gt, clusters)
    assert res["tp"] == 1
    assert res["fp"] == 0


def test_no_data_gives_nan_metrics():
    res = transforms.evaluate_detections(pd.DataFrame(), pd.DataFrame())
    assert res["tp"] == res["fp"] == res["fn"] == 0
    assert transforms.fmt_metric(res["precision"]) == "n/a"
    assert transforms.fmt_metric(res["f1"]) == "n/a"
