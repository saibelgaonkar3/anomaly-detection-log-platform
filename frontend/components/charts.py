"""Reusable Plotly figures for the dashboard.

Every function takes plain pandas data and returns a Plotly Figure —
no Streamlit calls, so they are unit-testable.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

PALETTE = [
    "#636EFA", "#EF553B", "#00CC96", "#AB63FA",
    "#FFA15A", "#19D3F3", "#FF6692", "#B6E880",
]

_LAYOUT = dict(
    margin=dict(l=20, r=20, t=30, b=20),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    template="plotly_white",
    height=340,
)


def _finish(fig: go.Figure, **overrides) -> go.Figure:
    layout = {**_LAYOUT, **overrides}
    fig.update_layout(**layout)
    return fig


def fig_requests_over_time(wide_df: pd.DataFrame) -> go.Figure:
    """Stacked bar of request counts per bucket, one trace per service."""
    if wide_df.empty:
        return _finish(go.Figure())
    long_df = wide_df.reset_index().melt(
        id_vars="bucket", var_name="service", value_name="requests"
    )
    fig = px.bar(
        long_df,
        x="bucket",
        y="requests",
        color="service",
        labels={"bucket": "Time", "requests": "Requests", "service": "Service"},
        color_discrete_sequence=PALETTE,
    )
    return _finish(fig, barmode="stack", hovermode="x unified")


def fig_latency_over_time(df: pd.DataFrame) -> go.Figure:
    """Average and p95 latency lines per bucket."""
    if df.empty:
        return _finish(go.Figure())
    plot_df = df.melt(
        id_vars="bucket",
        value_vars=["avg_latency_ms", "p95_latency_ms"],
        var_name="series",
        value_name="latency_ms",
    )
    plot_df["series"] = plot_df["series"].map(
        {"avg_latency_ms": "Average", "p95_latency_ms": "p95"}
    )
    fig = px.line(
        plot_df,
        x="bucket",
        y="latency_ms",
        color="series",
        labels={"bucket": "Time", "latency_ms": "Latency (ms)", "series": ""},
        color_discrete_sequence=[PALETTE[0], PALETTE[3]],
    )
    fig.update_traces(mode="lines+markers", line=dict(width=2))
    return _finish(fig, hovermode="x unified")


def fig_anomalies_over_time(df: pd.DataFrame) -> go.Figure:
    """Bar chart of anomaly counts per bucket (all services stacked)."""
    if df.empty or "anomalies" not in df.columns:
        return _finish(go.Figure())
    agg = df.groupby("bucket", as_index=False)["anomalies"].sum()
    fig = px.bar(
        agg,
        x="bucket",
        y="anomalies",
        labels={"bucket": "Time", "anomalies": "Anomalies"},
        color_discrete_sequence=[PALETTE[1]],
    )
    return _finish(fig, hovermode="x unified")


def fig_score_scatter(df: pd.DataFrame) -> go.Figure:
    """Anomaly score over time; flagged rows highlighted by symbol."""
    if df.empty:
        return _finish(go.Figure())
    plot_df = df.copy()
    plot_df["status"] = plot_df["is_anomaly"].map(
        {True: "Flagged anomaly", False: "Scored normal"}
    ).fillna("Scored normal")
    fig = px.scatter(
        plot_df,
        x="timestamp",
        y="anomaly_score",
        color="service",
        symbol="status",
        labels={
            "timestamp": "Time",
            "anomaly_score": "Anomaly score (|z|)",
            "service": "Service",
            "status": "",
        },
        color_discrete_sequence=PALETTE,
        opacity=0.75,
    )
    return _finish(fig, height=380)


def fig_count_by(
    df: pd.DataFrame, x: str, y: str, title: str, color: str | None = None,
    color_sequence: list[str] | None = None,
) -> go.Figure:
    """Generic horizontal-bar comparison figure."""
    if df.empty:
        return _finish(go.Figure())
    fig = px.bar(
        df,
        x=x,
        y=y,
        color=color,
        orientation="h",
        labels={x: x.replace("_", " ").title(), y: y.replace("_", " ").title()},
        color_discrete_sequence=color_sequence or PALETTE,
    )
    return _finish(fig, title=title, height=300, margin=dict(l=20, r=20, t=50, b=20))


def fig_confusion_matrix(tp: int, fp: int, fn: int) -> go.Figure:
    """2x2 event-level confusion heatmap (TN is undefined at event level)."""
    z = [[tp, fn], [fp, None]]
    text = [[str(tp), str(fn)], [str(fp), "n/a"]]
    fig = go.Figure(
        go.Heatmap(
            z=z,
            text=text,
            texttemplate="%{text}",
            x=["Predicted: anomaly", "Predicted: normal"],
            y=["Actual: anomaly", "Actual: normal"],
            colorscale=[[0, "#f7f7f7"], [1, "#636EFA"]],
            showscale=False,
            hoverinfo="skip",
        )
    )
    return _finish(
        fig,
        height=260,
        width=520,
        xaxis=dict(side="bottom"),
        yaxis=dict(autorange="reversed"),
        margin=dict(l=20, r=20, t=30, b=60),
    )
