"""Pure pandas transforms and evaluation logic (no Streamlit imports).

Everything here is unit-testable without a database or a running UI.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

# The traffic-generator injects these two anomaly types. Suspicious
# sequence detection is NOT implemented in the current detector (and the
# dashboard must not pretend it is), so it is excluded from evaluation.
EVALUABLE_TYPES = ("error_spike", "latency_spike")
EXCLUDED_TYPES = ("suspicious_sequence",)

# How long each injected anomaly lasts (mirrors traffic-generator timings).
GT_EVENT_WINDOW_SECONDS = {
    "error_spike": 25.0,      # hammers payments /charge for 25s
    "latency_spike": 20.0,    # slow_mode inventory /check for 20s
}

# Flagged rows closer than this (per service+endpoint) belong to the
# same detection "event" (the scorer emits per-10s-window flags).
CLUSTER_GAP_SECONDS = 30.0

# Ground-truth timestamps can precede the first flagged log slightly
# (the scorer runs in 30s batches), so allow a small look-back.
MATCH_TOLERANCE_BEFORE_SECONDS = 5.0

_SERVICE_RE = re.compile(r"\b([a-z][a-z0-9]*-service)\b")


# ---------------------------------------------------------------------------
# Time-bucket sizing
# ---------------------------------------------------------------------------
def bucket_seconds_for_range(start: Optional[datetime], end: Optional[datetime]) -> int:
    """Pick a sensible chart bucket size for the selected time range."""
    if start is None or end is None:
        return 3600
    span = max((end - start).total_seconds(), 1.0)
    if span <= 3600:
        return 30
    if span <= 6 * 3600:
        return 60
    if span <= 24 * 3600:
        return 300
    return 3600


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Overview / health helpers
# ---------------------------------------------------------------------------
def rate(numerator: float, denominator: float) -> float:
    """Safe percentage (0.0 when the denominator is zero)."""
    if not denominator:
        return 0.0
    return 100.0 * numerator / denominator


def anomaly_rate(anomaly_count: float, scored_requests: float, total_requests: float) -> float:
    """Anomaly rate as a percentage of SCORED rows.

    Rows not yet scored by the Spark job (anomaly_score IS NULL) are
    excluded from the denominator so partially-scored recent data does
    not dilute the rate.
    """
    return rate(anomaly_count, scored_requests if scored_requests else total_requests)


def prepare_traffic_wide(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot long per-service counts into a wide requests-per-bucket frame."""
    if df.empty:
        return df
    wide = df.pivot_table(
        index="bucket", columns="service", values="requests", aggfunc="sum", fill_value=0
    )
    return wide.sort_index()


# ---------------------------------------------------------------------------
# Ground-truth helpers
# ---------------------------------------------------------------------------
def eligible_events(gt: pd.DataFrame) -> pd.DataFrame:
    """Keep only ground-truth events the current detector can produce."""
    if gt.empty or "anomaly_type" not in gt.columns:
        return gt.iloc[0:0].copy() if not gt.empty else gt.copy()
    return gt[gt["anomaly_type"].isin(EVALUABLE_TYPES)].copy()


def event_service(detail: object) -> Optional[str]:
    """Best-effort extraction of the target service from a GT detail string."""
    if not isinstance(detail, str):
        return None
    match = _SERVICE_RE.search(detail)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Detection-event clustering
# ---------------------------------------------------------------------------
def cluster_flagged_events(
    flagged: pd.DataFrame, gap_seconds: float = CLUSTER_GAP_SECONDS
) -> pd.DataFrame:
    """Group flagged log rows into discrete detection events.

    Rows are clustered per (service, endpoint); a gap larger than
    `gap_seconds` starts a new cluster. Returns one row per cluster with
    start/end timestamps, row count and the max anomaly score.
    """
    cols = ["timestamp", "service", "endpoint", "anomaly_score"]
    if flagged.empty:
        return pd.DataFrame(columns=["service", "endpoint", "start", "end", "rows", "max_score"])
    df = flagged[cols].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values(["service", "endpoint", "timestamp"])

    clusters = []
    for (service, endpoint), group in df.groupby(["service", "endpoint"], dropna=False):
        cluster_start = None
        cluster_end = None
        cluster_rows = 0
        cluster_max = 0.0
        for _, row in group.iterrows():
            ts = row["timestamp"]
            score = row["anomaly_score"] if pd.notna(row["anomaly_score"]) else 0.0
            if cluster_start is None or (ts - cluster_end).total_seconds() > gap_seconds:
                if cluster_start is not None:
                    clusters.append(
                        {
                            "service": service,
                            "endpoint": endpoint,
                            "start": cluster_start,
                            "end": cluster_end,
                            "rows": cluster_rows,
                            "max_score": cluster_max,
                        }
                    )
                cluster_start = ts
                cluster_rows = 0
                cluster_max = 0.0
            cluster_end = ts
            cluster_rows += 1
            cluster_max = max(cluster_max, float(score))
        if cluster_start is not None:
            clusters.append(
                {
                    "service": service,
                    "endpoint": endpoint,
                    "start": cluster_start,
                    "end": cluster_end,
                    "rows": cluster_rows,
                    "max_score": cluster_max,
                }
            )
    return pd.DataFrame(
        clusters,
        columns=["service", "endpoint", "start", "end", "rows", "max_score"],
    )


# ---------------------------------------------------------------------------
# Evaluation: match ground-truth events to detection events
# ---------------------------------------------------------------------------
def evaluate_detections(
    gt_events: pd.DataFrame, detections: pd.DataFrame
) -> dict:
    """Event-level confusion matrix + precision / recall / F1.

    - A ground-truth event is a TP when at least one detection event
      overlaps its expected window [ts - tolerance, ts + duration] and,
      when the detail string names a service, targets that service.
    - FN: a ground-truth event with no matching detection.
    - FP: a detection event that matches no ground-truth event.

    Returns a dict with counts, metrics and annotated copies of both
    inputs (`gt` and `detections` DataFrames).
    """
    gt = gt_events.copy() if gt_events is not None else pd.DataFrame()
    detections = detections.copy() if detections is not None else pd.DataFrame()

    if not gt.empty:
        gt["timestamp"] = pd.to_datetime(gt["timestamp"], utc=True)
        gt["target_service"] = (
            gt["detail"].map(event_service) if "detail" in gt.columns else None
        )
        gt["matched"] = False
    if not detections.empty:
        detections["matched"] = False

    gt_rows = list(gt.iterrows()) if not gt.empty else []
    for gt_idx, gt_row in gt_rows:
        window = GT_EVENT_WINDOW_SECONDS.get(
            gt_row.get("anomaly_type", ""), 60.0
        )
        earliest = gt_row["timestamp"] - timedelta(
            seconds=MATCH_TOLERANCE_BEFORE_SECONDS
        )
        latest = gt_row["timestamp"] + timedelta(seconds=window)
        target = gt_row.get("target_service")

        for det_idx, det_row in detections.iterrows():
            if det_row["matched"]:
                continue
            if target and det_row["service"] != target:
                continue
            # overlap of [det.start, det.end] with [earliest, latest]
            if det_row["end"] >= earliest and det_row["start"] <= latest:
                gt.at[gt_idx, "matched"] = True
                detections.at[det_idx, "matched"] = True
                break

    tp = int(gt["matched"].sum()) if not gt.empty else 0
    fn = int((~gt["matched"]).sum()) if not gt.empty else 0
    fp = int((~detections["matched"]).sum()) if not detections.empty else 0

    precision = tp / (tp + fp) if (tp + fp) else math.nan
    recall = tp / (tp + fn) if (tp + fn) else math.nan
    if pd.notna(precision) and pd.notna(recall) and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = math.nan

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "gt": gt,
        "detections": detections,
    }


def fmt_metric(value: float, digits: int = 3) -> str:
    """Format a metric that may be NaN (undefined)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:.{digits}f}"
