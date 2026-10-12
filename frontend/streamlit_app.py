"""Overview page — entry point for the Streamlit multipage app.

Run with:
    streamlit run frontend/streamlit_app.py
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from components import charts
from components.cards import fmt_float, fmt_int, fmt_pct, metric_row
from components.sidebar import render_sidebar
from core import db, queries, transforms

st.set_page_config(
    page_title="Overview — Log Anomaly Detection",
    page_icon=" ",
    layout="wide",
)

st.title(" Overview")
st.caption(
    "Live view of the microservice request pipeline: logs are shipped from "
    "Docker containers into PostgreSQL and scored for anomalies by the "
    "Spark MapReduce job (every ~30s)."
)

filters = render_sidebar()
bucket = transforms.bucket_seconds_for_range(filters.start, filters.end)

with db.db_error_handler():
    stats = queries.summary_stats(filters.start, filters.end)
    traffic = queries.traffic_over_time(filters.start, filters.end, bucket)
    latency = queries.latency_over_time(filters.start, filters.end, bucket)

if stats.empty:
    st.info(
        "No log rows found for the selected time range "
        f"(**{filters.preset}**). If the backend stack is running, try a "
        "wider time range."
    )
    st.stop()

row = stats.iloc[0]
total = int(row["total_requests"] or 0)
scored = int(row["scored_requests"] or 0)
anomalies = int(row["anomaly_count"] or 0)
errors = int(row["error_count"] or 0)
unscored = max(total - scored, 0)

st.subheader("Key metrics")
metric_row(
    [
        {
            "label": "Total requests",
            "value": fmt_int(total),
            "help": "Log rows ingested into PostgreSQL for the selected range.",
        },
        {
            "label": "Anomalies detected",
            "value": fmt_int(anomalies),
            "help": "Rows flagged by the Spark scorer (is_anomaly = TRUE).",
        },
        {
            "label": "Anomaly rate",
            "value": fmt_pct(anomalies, scored),
            "help": (
                "Anomalies as a percentage of SCORED rows. The scorer runs "
                "every ~30s, so the most recent rows may not be scored yet."
            ),
        },
        {
            "label": "Avg latency",
            "value": fmt_float(row["avg_latency_ms"], 1, " ms"),
        },
        {
            "label": "p95 latency",
            "value": fmt_float(row["p95_latency_ms"], 1, " ms"),
        },
        {
            "label": "Error rate",
            "value": fmt_pct(errors, total),
            "help": "Requests with HTTP status >= 400.",
        },
    ],
    columns=3,
)

if unscored:
    st.caption(
        f"ℹ {unscored} row(s) in this window are not scored yet "
        "(pending Spark batch); they are excluded from the anomaly rate."
    )
last_ts = row["last_timestamp"]
if last_ts is not None and not pd.isna(last_ts):
    st.caption(f"Latest log row: **{last_ts}**  •  chart bucket: **{bucket}s**")

st.subheader("Traffic")
if traffic.empty:
    st.info("No traffic data for this range.")
else:
    st.plotly_chart(
        charts.fig_requests_over_time(transforms.prepare_traffic_wide(traffic)),
        use_container_width=True,
    )

left, right = st.columns(2, gap="medium")
with left:
    st.subheader("Latency")
    if latency.empty:
        st.info("No latency data for this range.")
    else:
        st.plotly_chart(charts.fig_latency_over_time(latency), use_container_width=True)
with right:
    st.subheader("Anomalies over time")
    if traffic.empty or "anomalies" not in traffic.columns:
        st.info("No anomaly data for this range.")
    else:
        st.plotly_chart(charts.fig_anomalies_over_time(traffic), use_container_width=True)
