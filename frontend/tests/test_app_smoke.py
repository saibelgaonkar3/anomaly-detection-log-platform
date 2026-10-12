"""Smoke tests: execute every dashboard page in Streamlit's AppTest harness.

No database is available in CI/dev-without-Docker, so these tests assert
that each page RENDERS GRACEFULLY (friendly error/empty states, no
unhandled exceptions) when PostgreSQL is unreachable.
"""

from pathlib import Path

import pytest

st = pytest.importorskip("streamlit.testing.v1", reason="streamlit AppTest unavailable")

FRONTEND = Path(__file__).resolve().parents[1]
PAGES = [
    FRONTEND / "streamlit_app.py",
    FRONTEND / "pages" / "1_Log_Explorer.py",
    FRONTEND / "pages" / "2_Anomaly_Investigation.py",
    FRONTEND / "pages" / "3_Evaluation.py",
    FRONTEND / "pages" / "4_Service_Health.py",
]


@pytest.mark.parametrize("script", PAGES, ids=lambda p: p.stem)
def test_page_renders_without_unhandled_exception(script, monkeypatch):
    # Make sure the DB is genuinely unreachable during the test so the
    # graceful-degradation path is what gets exercised.
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "1")
    monkeypatch.setattr("socket.getdefaulttimeout", lambda: 1, raising=False)

    at = st.AppTest.from_file(str(script), default_timeout=120)
    at.run()

    assert not at.exception, f"{script.stem} raised:\n{at.exception}"
