"""Shared sidebar: time-range filter, connection status, refresh button."""

from __future__ import annotations

from datetime import timedelta
from typing import NamedTuple

import streamlit as st

from core import db
from core.transforms import utc_now

RANGE_PRESETS = {
    "Last 15 minutes": timedelta(minutes=15),
    "Last hour": timedelta(hours=1),
    "Last 6 hours": timedelta(hours=6),
    "Last 24 hours": timedelta(hours=24),
    "Last 7 days": timedelta(days=7),
    "All time": None,
}

DEFAULT_PRESET = "Last 24 hours"


class Filters(NamedTuple):
    start: object  # datetime (UTC) or None for "all time"
    end: object
    preset: str


@st.cache_data(ttl=60, show_spinner=False)
def _connection_status() -> str:
    return db.check_connection()


def render_sidebar() -> Filters:
    st.sidebar.title(" Log Anomaly Detection")
    st.sidebar.caption("Cloud log anomaly platform — live dashboard")

    preset = st.sidebar.selectbox(
        "Time range",
        list(RANGE_PRESETS),
        index=list(RANGE_PRESETS).index(DEFAULT_PRESET),
        help="All charts and tables use this window.",
    )
    delta = RANGE_PRESETS[preset]
    end = utc_now()
    start = None if delta is None else end - delta

    if st.sidebar.button(" Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    status = _connection_status()
    with st.sidebar.expander(" Postgres connection", expanded=(status != "connected")):
        st.code(db.connection_target(), language=None)
        if status == "connected":
            st.success("Connected")
        else:
            st.error(status)
            st.caption(
                "Start the backend stack (`docker compose up -d`) or set the "
                "`DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` "
                "environment variables."
            )

    return Filters(start=start, end=end, preset=preset)
