"""Log Explorer — searchable, filterable view of ingested logs."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Make `core` / `components` importable when Streamlit runs this page file.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.sidebar import render_sidebar  # noqa: E402
from core import db, queries  # noqa: E402

st.set_page_config(page_title="Log Explorer", page_icon=" ", layout="wide")
st.title(" Log Explorer")
st.caption("Search and filter every log row shipped into PostgreSQL.")

filters = render_sidebar()

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
with db.db_error_handler():
    options = queries.filter_options()

services_available = options["service"].dropna().unique().tolist() if not options.empty else []
endpoints_available = (
    options["endpoint"].dropna().unique().tolist() if not options.empty else []
)

f1, f2, f3, f4 = st.columns([2, 2, 2, 2])
with f1:
    services = st.multiselect("Service", services_available)
with f2:
    endpoints = st.multiselect("Endpoint", endpoints_available)
with f3:
    status_classes = st.multiselect(
        "Status class", ["2xx", "3xx", "4xx", "5xx"],
        help="Filter by HTTP status-code class.",
    )
with f4:
    anomaly_filter = st.radio(
        "Anomalies", ["All", "Only anomalies", "Only normal"], horizontal=True,
        help="'Only normal' includes rows not yet scored by the Spark job.",
    )

search = st.text_input(
    "Search message or request ID", placeholder="e.g. payment gateway timeout"
)

page_size = st.selectbox("Rows per page", [50, 100, 200, 500], index=1)

if "log_page" not in st.session_state:
    st.session_state.log_page = 0

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
with db.db_error_handler():
    total = queries.count_logs(
        filters.start, filters.end, services, endpoints,
        status_classes, anomaly_filter, search or None,
    )
    logs = queries.fetch_logs(
        filters.start, filters.end, services, endpoints,
        status_classes, anomaly_filter, search or None,
        limit=page_size, offset=st.session_state.log_page * page_size,
    )

pages = max((total + page_size - 1) // page_size, 1)
st.session_state.log_page = min(st.session_state.log_page, pages - 1)

nav1, nav2, nav3 = st.columns([1, 2, 1])
with nav1:
    if st.button("⬅ Previous", disabled=st.session_state.log_page == 0):
        st.session_state.log_page -= 1
        st.rerun()
with nav2:
    st.caption(
        f"Page {st.session_state.log_page + 1} of {pages} — "
        f"{total:,} matching row(s)"
    )
with nav3:
    if st.button("Next ➡", disabled=st.session_state.log_page >= pages - 1):
        st.session_state.log_page += 1
        st.rerun()

# ---------------------------------------------------------------------------
# Table + export
# ---------------------------------------------------------------------------
if logs.empty:
    st.info("No log rows match the current filters.")
else:
    display = logs.copy()
    display["is_anomaly"] = display["is_anomaly"].map(
        {True: " Yes", False: " No"}
    ).fillna("⏳ unscored")
    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        column_config={
            "id": st.column_config.NumberColumn("ID", width="small"),
            "timestamp": st.column_config.DatetimeColumn(
                "Time (UTC)", format="YYYY-MM-DD HH:mm:ss.SSS", width="medium"
            ),
            "service": st.column_config.TextColumn("Service", width="small"),
            "level": st.column_config.TextColumn("Level", width="small"),
            "endpoint": st.column_config.TextColumn("Endpoint", width="small"),
            "latency_ms": st.column_config.NumberColumn(
                "Latency (ms)", format="%.1f", width="small"
            ),
            "status_code": st.column_config.NumberColumn("Status", width="small"),
            "message": st.column_config.TextColumn("Message", width="large"),
            "request_id": st.column_config.TextColumn("Request ID", width="medium"),
            "anomaly_score": st.column_config.NumberColumn(
                "Anomaly score", format="%.4f", width="small"
            ),
            "is_anomaly": st.column_config.TextColumn("Anomaly", width="small"),
        },
    )

    csv = logs.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇ Download this page as CSV",
        data=csv,
        file_name="logs_page.csv",
        mime="text/csv",
    )
