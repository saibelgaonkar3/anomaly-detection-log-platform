"""Tests for the ground-truth file loader (no database required)."""

import json

import pandas as pd

from core import ground_truth


def test_load_from_file_parses_jsonl(tmp_path):
    path = tmp_path / "ground_truth.log"
    entries = [
        {
            "timestamp": "2026-01-01T12:00:00+00:00",
            "anomaly_type": "error_spike",
            "detail": "hammering payments-service /charge for 25s",
        },
        {
            "timestamp": "2026-01-01T12:03:00+00:00",
            "anomaly_type": "latency_spike",
            "detail": "inventory-service /check slow_mode for 20s",
        },
    ]
    path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\nnot-json\n\n", encoding="utf-8"
    )

    df, skipped = ground_truth.load_from_file(path)

    assert len(df) == 2
    assert skipped == 1
    assert list(df.columns) == ["timestamp", "anomaly_type", "detail"]
    assert str(df["timestamp"].dt.tz) == "UTC"
    assert list(df["anomaly_type"]) == ["error_spike", "latency_spike"]


def test_load_from_file_empty(tmp_path):
    path = tmp_path / "empty.log"
    path.write_text("", encoding="utf-8")
    df, skipped = ground_truth.load_from_file(path)
    assert df.empty
    assert skipped == 0


def test_candidate_paths_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom.log"
    monkeypatch.setenv("GROUND_TRUTH_LOG", str(custom))
    paths = ground_truth.candidate_paths()
    assert paths[0] == custom


def test_candidate_paths_default_order(monkeypatch):
    monkeypatch.delenv("GROUND_TRUTH_LOG", raising=False)
    paths = ground_truth.candidate_paths()
    # repo-level ground-truth folder comes before the cwd and /var/log
    repo_path = ground_truth._REPO_ROOT / "ground-truth" / "ground_truth.log"
    assert paths[0] == repo_path
    assert any(
        p.name == "ground_truth.log" and "var" in p.parts and "log" in p.parts
        for p in paths
    )
    # no duplicates
    assert len(paths) == len({str(p) for p in paths})


def test_load_ground_truth_reports_missing_sources(monkeypatch, tmp_path):
    # Point every candidate at a non-existent file and force the DB
    # fallback to fail without a real database.
    monkeypatch.setenv("GROUND_TRUTH_LOG", str(tmp_path / "nope.log"))
    monkeypatch.setattr(
        ground_truth, "candidate_paths", lambda: [tmp_path / "nope.log"]
    )

    from core import db as db_module

    def _raise(_sql, _params=None):
        raise db_module.DatabaseUnavailableError("no db in tests")

    monkeypatch.setattr(ground_truth.queries, "fetch_ground_truth_rows", _raise)

    result = ground_truth.load_ground_truth()
    assert not result.available
    assert result.source == "unavailable"
    assert any("not found" in e for e in result.errors)
