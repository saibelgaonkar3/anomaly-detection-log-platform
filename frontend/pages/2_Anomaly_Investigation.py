"""Anomaly Investigation — inspect flagged requests and their scores."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components import charts  # noqa: E402
from components.cards import fmt_float, fmt_int, fmt_pct, metric_row  # noqa: E402
from components.sidebar import render_sidebar  # noqa: E402
from core import db, queries, transforms  # noqa: E402

st.set_page_config(page_title="Anomaly Investigation", page_icon=" ", layout="wide")
st.title(" Anomaly Investigation")
st.caption(
    "Requests flagged by the Spark scorer (latency z-score > 3 or error "
    "spike in a 10-second window)."
)

filters = render_sidebar()

with db.db_error_handler():
    options = queries.filter_options()
services_available = (
    options["service"].dropna().unique().tolist() if not options.empty else []
)

f1, f2, f3 = st.columns([2, 2, 3])
with f1:
    services = st.multiselect("Service", services_available)
with f2:
    min_score = st.slider(
        "Minimum anomaly score", 0.0, 10.0, 0.0, 0.1,
        help="Score is |latency z-score| within the 10s window.",
    )
with f3:
    max_rows = st.select_slider(
        "Max rows", options=[100, 250, 500, 1000], value=500
    )

with db.db_error_handler():
    anomalies = queries.fetch_anomalies(
        filters.start, filters.end, services, min_score, limit=max_rows
    )
    scored = queries.fetch_scored_rows(filters.start, filters.end)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
if anomalies.empty:
    st.info(
        "No flagged anomalies in the selected time range "
        f"(**{filters.preset}**). Note the scorer runs every ~30s, so very "
        "recent requests may not be scored yet."
    )
else:
    by_service = anomalies.groupby("service").size().sort_values(ascending=False)
    top_service = by_service.index[0] if not by_service.empty else "—"
    metric_row(
        [
            {
                "label": "Flagged requests",
                "value": fmt_int(len(anomalies)),
                "help": f"Capped at {max_rows} rows for this view.",
            },
            {
                "label": "Avg anomaly score",
                "value": fmt_float(anomalies["anomaly_score"].mean(), 3),
            },
            {
                "label": "Max anomaly score",
                "value": fmt_float(anomalies["anomaly_score"].max(), 3),
            },
            {
                "label": "Most affected service",
                "value": top_service,
                "help": "Service with the most flagged rows in this view.",
            },
            {
                "label": "4xx/5xx among flagged",
                "value": fmt_pct(
                    (anomalies["status_code"] >= 400).sum(), len(anomalies)
                ),
            },
            {
                "label": "Avg latency (flagged)",
                "value": fmt_float(anomalies["latency_ms"].mean(), 1, " ms"),
            },
        ],
        columns=3,
    )

    # ------------------------------------------------------------------
    # Charts
    # ------------------------------------------------------------------
    left, right = st.columns(2, gap="medium")
    with left:
        st.subheader("Anomaly scores over time")
        if scored.empty:
            st.info("No scored rows in this range.")
        else:
            st.plotly_chart(charts.fig_score_scatter(scored), use_container_width=True)
    with right:
        st.subheader("Flagged rows per service")
        service_counts = (
            anomalies.groupby("service").size().reset_index(name="flagged")
            if not anomalies.empty
            else anomalies
        )
        st.plotly_chart(
            charts.fig_count_by(
                service_counts.sort_values("flagged"),
                x="flagged", y="service", title="",
            ),
            use_container_width=True,
        )

    # ------------------------------------------------------------------
    # Table of recent anomalies with expandable details
    # ------------------------------------------------------------------
    st.subheader("Recent anomalies")
    for _, row in anomalies.sort_values("timestamp", ascending=False).iterrows():
        title = (
            f"{row['timestamp']} · {row['service']} {row['endpoint'] or ''} · "
            f"score {transforms.fmt_metric(row['anomaly_score'])}"
        )
        with st.expander(title):
            c1, c2, c3 = st.columns(3)
            c1.metric("Status code", fmt_int(row["status_code"]))
            c2.metric("Latency", fmt_float(row["latency_ms"], 1, " ms"))
            c3.metric("Anomaly score", transforms.fmt_metric(row["anomaly_score"]))
            st.write("**Message:**", row["message"] or "—")
            st.write("**Request ID:**", row["request_id"] or "—")

    csv = anomalies.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇ Download flagged rows as CSV",
        data=csv,
        file_name="anomalies.csv",
        mime="text/csv",
    )
