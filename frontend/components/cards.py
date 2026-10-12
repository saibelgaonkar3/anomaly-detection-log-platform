"""Small formatting helpers and KPI card rows."""

from __future__ import annotations

from typing import Sequence

import streamlit as st


def fmt_int(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—"


def fmt_float(value, digits: int = 1, suffix: str = "") -> str:
    try:
        return f"{float(value):,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def fmt_pct(numerator, denominator, digits: int = 2) -> str:
    try:
        num, den = float(numerator), float(denominator)
    except (TypeError, ValueError):
        return "—"
    if not den:
        return "—"
    return f"{100.0 * num / den:.{digits}f}%"


def metric_row(metrics: Sequence[dict], columns: int = 3) -> None:
    """Render a row of st.metric cards.

    Each metric dict: label, value, delta (optional), help (optional).
    """
    for i in range(0, len(metrics), columns):
        cols = st.columns(columns, gap="medium")
        for col, metric in zip(cols, metrics[i : i + columns]):
            with col:
                st.metric(
                    label=metric["label"],
                    value=metric.get("value", "—"),
                    delta=metric.get("delta"),
                    help=metric.get("help"),
                )
