"""Evaluation — compare detector output against injected ground truth.

Methodology (event level):
- The traffic-generator injects anomalies on a schedule and records each
  injection as a ground-truth event (timestamp + type + detail).
- Flagged log rows are clustered into detection events (same
  service+endpoint, gaps <= 30s).
- A ground-truth event counts as a TRUE POSITIVE when a detection event
  overlaps its expected window [injection time, injection time +
  duration] and targets the same service (when the detail names one).
- Unmatched ground-truth events are FALSE NEGATIVES; unmatched detection
  events are FALSE POSITIVES (event-level precision is therefore
  optimistic in long windows with unscored noise).
- `suspicious_sequence` events are EXCLUDED: that detection type is not
  implemented in the current Spark scorer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components import charts  # noqa: E402
from components.sidebar import render_sidebar  # noqa: E402
from core import db, ground_truth, queries, transforms  # noqa: E402

st.set_page_config(page_title="Evaluation", page_icon=" ", layout="wide")
st.title(" Evaluation")
st.caption("Detector performance against the traffic-generator's ground truth.")

filters = render_sidebar()

# ---------------------------------------------------------------------------
# Ground truth
# ---------------------------------------------------------------------------
gt_result = ground_truth.load_ground_truth(filters.start, filters.end)
gt = gt_result.df.copy()

# The file source covers all time; clip to the selected range here.
if gt_result.source.startswith("file:") and not gt.empty:
    if filters.start is not None:
        gt = gt[gt["timestamp"] >= pd.Timestamp(filters.start)]
    if filters.end is not None:
        gt = gt[gt["timestamp"] <= pd.Timestamp(filters.end)]

source_badge = gt_result.source if gt_result.available else "unavailable"
st.caption(f"**Ground-truth source:** `{source_badge}`")

if not gt_result.available:
    st.warning(
        "No ground-truth data available — evaluation cannot be computed.\n\n"
        "Checked (in order):\n"
        + "\n".join(f"- `{e}`" if e.startswith("not found") else f"- {e}"
                    for e in gt_result.errors[:8])
        + "\n\nThe traffic-generator writes this data while the backend stack "
        "runs (`docker compose up -d`)."
    )
    st.stop()

excluded = gt[~gt["anomaly_type"].isin(transforms.EVALUABLE_TYPES)]
gt_eval = transforms.eligible_events(gt)

if not excluded.empty:
    counts = excluded["anomaly_type"].value_counts().to_dict()
    st.info(
        f"ℹ **{len(excluded)}** ground-truth event(s) excluded from evaluation: "
        + ", ".join(f"`{k}` × {v}" for k, v in counts.items())
        + ". Suspicious-sequence detection is **not implemented** in the current "
        "scorer, so counting these would unfairly lower recall."
    )

if gt_eval.empty:
    st.info("No evaluable ground-truth events (error_spike / latency_spike) in range.")
    st.stop()

# ---------------------------------------------------------------------------
# Detector output + matching
# ---------------------------------------------------------------------------
with db.db_error_handler():
    flagged = queries.fetch_flagged_rows(filters.start, filters.end)

detections = transforms.cluster_flagged_events(flagged)
results = transforms.evaluate_detections(gt_eval, detections)

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
st.subheader("Detector performance (event level)")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Precision", transforms.fmt_metric(results["precision"]),
          help="Of detection events, how many matched a real injected anomaly.")
m2.metric("Recall", transforms.fmt_metric(results["recall"]),
          help="Of injected anomalies, how many were detected.")
m3.metric("F1 score", transforms.fmt_metric(results["f1"]))
m4.metric(
    "Events",
    f"{results['tp']} / {results['fp']} / {results['fn']}",
    help="TP / FP / FN (event level).",
)

c1, c2 = st.columns([1, 1], gap="large")
with c1:
    st.plotly_chart(
        charts.fig_confusion_matrix(results["tp"], results["fp"], results["fn"]),
        use_container_width=True,
    )
with c2:
    st.caption(
        "**How to read this:** each injected anomaly is one actual-positive; "
        "each cluster of flagged logs (same service+endpoint, gaps ≤ 30s) is "
        "one predicted-positive. True negatives are undefined at event level. "
        "Recent rows may still be awaiting the next Spark scoring batch "
        "(~30s), which can temporarily lower recall."
    )
    st.caption(
        f"Ground-truth events evaluated: **{len(gt_eval)}** • "
        f"Detection events: **{len(detections)}** • "
        f"Flagged log rows clustered: **{len(flagged)}**"
    )

# ---------------------------------------------------------------------------
# Detail tables
# ---------------------------------------------------------------------------
left, right = st.columns(2, gap="large")

with left:
    st.subheader("Ground-truth events")
    gt_view = results["gt"].copy()
    if not gt_view.empty:
        gt_view["outcome"] = gt_view["matched"].map(
            {True: "✅ detected", False: "❌ missed"}
        )
        st.dataframe(
            gt_view[["timestamp", "anomaly_type", "detail", "target_service", "outcome"]]
            .sort_values("timestamp", ascending=False),
            hide_index=True,
            use_container_width=True,
        )
        missed = gt_view[~gt_view["matched"]]
        if not missed.empty:
            with st.expander(f" Missed events ({len(missed)})"):
                st.dataframe(
                    missed[["timestamp", "anomaly_type", "detail"]]
                    .sort_values("timestamp", ascending=False),
                    hide_index=True,
                    use_container_width=True,
                )

with right:
    st.subheader("Detection events")
    if detections.empty:
        st.info("No flagged rows were clustered into detection events.")
    else:
        det_view = results["detections"].copy()
        det_view["outcome"] = det_view["matched"].map(
            {True: "✅ matched ground truth", False: "⚠ possible false positive"}
        )
        st.dataframe(
            det_view[["start", "end", "service", "endpoint", "rows", "max_score", "outcome"]]
            .sort_values("max_score", ascending=False),
            hide_index=True,
            use_container_width=True,
            column_config={
                "start": st.column_config.DatetimeColumn(
                    "Start (UTC)", format="YYYY-MM-DD HH:mm:ss"
                ),
                "end": st.column_config.DatetimeColumn(
                    "End (UTC)", format="YYYY-MM-DD HH:mm:ss"
                ),
                "max_score": st.column_config.NumberColumn("Max score", format="%.3f"),
            },
        )

with st.expander(" Evaluation methodology & caveats"):
    st.markdown(
        """
- **Ground truth source order:** `GROUND_TRUTH_LOG` env var →
  `<repo>/ground-truth/ground_truth.log` → `./ground_truth.log` →
  `/var/log/ground_truth.log` → `ground_truth` database table.
- **Evaluable types:** `error_spike` and `latency_spike` only.
  `suspicious_sequence` is excluded because the current Spark scorer has
  no session/sequence detection.
- **Windows:** `error_spike` events expect detections within 25s of the
  injection, `latency_spike` within 20s (mirroring the traffic
  generator), with a 5s look-back for scorer batch delay.
- **Detection events:** flagged rows are clustered per service+endpoint;
  a gap > 30s starts a new event.
- **Caveat:** ground truth records *when* anomalies were injected, not
  which log rows caused them, so matching is time-window based and
  approximate. Precision is event-level (unmatched clusters count as
  false positives even if they were genuine organic errors).
        """
    )
