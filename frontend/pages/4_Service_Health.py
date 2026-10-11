"""Service Health — per-microservice KPIs and comparisons."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components import charts  # noqa: E402
from components.cards import fmt_float, fmt_int, fmt_pct  # noqa: E402
from components.sidebar import render_sidebar  # noqa: E402
from core import db, queries, transforms  # noqa: E402

st.set_page_config(page_title="Service Health", page_icon=" ", layout="wide")
st.title(" Service Health")
st.caption("Request volume, latency, errors and anomalies per microservice.")

filters = render_sidebar()

with db.db_error_handler():
    stats = queries.service_stats(filters.start, filters.end)

if stats.empty:
    st.info(
        f"No log rows found for **{filters.preset}**. Start the backend stack "
        "and generate some traffic, then refresh."
    )
    st.stop()

stats = stats.sort_values("service").reset_index(drop=True)

# ---------------------------------------------------------------------------
# Per-service cards
# ---------------------------------------------------------------------------
st.subheader("Microservices")
for i in range(0, len(stats), 4):
    cols = st.columns(4, gap="medium")
    for col, (_, row) in zip(cols, stats.iloc[i : i + 4].iterrows()):
        with col:
            st.markdown(f"####  {row['service']}")
            st.metric("Requests", fmt_int(row["requests"]))
            st.metric(
                "Avg latency",
                fmt_float(row["avg_latency_ms"], 1, " ms"),
            )
            st.metric(
                "p95 latency",
                fmt_float(row["p95_latency_ms"], 1, " ms"),
            )
            st.metric(
                "Error rate",
                fmt_pct(row["errors"], row["requests"]),
                help="Requests with HTTP status >= 400.",
            )
            st.metric(
                "Anomalies",
                fmt_int(row["anomalies"]),
                help="Flagged by the Spark scorer (is_anomaly = TRUE).",
            )

# ---------------------------------------------------------------------------
# Comparison charts
# ---------------------------------------------------------------------------
st.subheader("Comparisons")

g1, g2 = st.columns(2, gap="medium")
with g1:
    st.plotly_chart(
        charts.fig_count_by(
            stats.sort_values("requests"), x="requests", y="service",
            title="Request volume",
        ),
        use_container_width=True,
    )
with g2:
    st.plotly_chart(
        charts.fig_count_by(
            stats.sort_values("p95_latency_ms"), x="p95_latency_ms",
            y="service", title="p95 latency (ms)",
        ),
        use_container_width=True,
    )

g3, g4 = st.columns(2, gap="medium")
with g3:
    err = stats.copy()
    err["error_rate_pct"] = err.apply(
        lambda r: transforms.rate(r["errors"], r["requests"]), axis=1
    ).round(2)
    st.plotly_chart(
        charts.fig_count_by(
            err.sort_values("error_rate_pct"), x="error_rate_pct",
            y="service", title="Error rate (%)",
        ),
        use_container_width=True,
    )
with g4:
    anom = stats.copy()
    anom["anomaly_rate_pct"] = anom.apply(
        lambda r: transforms.anomaly_rate(r["anomalies"], r["scored_requests"], r["requests"]),
        axis=1,
    ).round(2)
    st.plotly_chart(
        charts.fig_count_by(
            anom.sort_values("anomaly_rate_pct"), x="anomaly_rate_pct",
            y="service", title="Anomaly rate (% of scored rows)",
        ),
        use_container_width=True,
    )

# ---------------------------------------------------------------------------
# Raw table
# ---------------------------------------------------------------------------
st.subheader("Details")
table = stats.copy()
table["error_rate_pct"] = table.apply(
    lambda r: round(transforms.rate(r["errors"], r["requests"]), 2), axis=1
)
table["anomaly_rate_pct"] = table.apply(
    lambda r: round(
        transforms.anomaly_rate(r["anomalies"], r["scored_requests"], r["requests"]), 2
    ),
    axis=1,
)
table["avg_latency_ms"] = table["avg_latency_ms"].round(1)
table["p95_latency_ms"] = table["p95_latency_ms"].round(1)
st.dataframe(
    table[
        [
            "service", "requests", "scored_requests", "errors", "error_rate_pct",
            "avg_latency_ms", "p95_latency_ms", "anomalies", "anomaly_rate_pct",
        ]
    ],
    hide_index=True,
    use_container_width=True,
)
st.download_button(
    "⬇ Download service stats as CSV",
    data=table.to_csv(index=False).encode("utf-8"),
    file_name="service_stats.csv",
    mime="text/csv",
)
