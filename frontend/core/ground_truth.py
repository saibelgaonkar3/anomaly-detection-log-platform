"""Ground-truth loading: file first, database table as fallback.

The traffic-generator writes every injected anomaly to
`/var/log/ground_truth.log` INSIDE its container and bind-mounts the
host folder `./ground-truth` there. When the dashboard runs on the host
the same file therefore lives at `<repo>/ground-truth/ground_truth.log`.

Search order:
1. `GROUND_TRUTH_LOG` environment variable (explicit path).
2. `<repo>/ground-truth/ground_truth.log` (relative to this file).
3. `./ground_truth.log` relative to the current working directory.
4. `/var/log/ground_truth.log` (inside the container / Linux hosts).
5. Fallback: the `ground_truth` table in PostgreSQL.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from core import db, queries

_REPO_ROOT = Path(__file__).resolve().parents[2]

GT_COLUMNS = ["timestamp", "anomaly_type", "detail"]


@dataclass
class GroundTruthResult:
    df: pd.DataFrame
    source: str  # human-readable label, e.g. "file: C:\\...\\ground_truth.log"
    errors: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return not self.df.empty


def candidate_paths() -> list[Path]:
    paths: list[Path] = []
    env_path = os.environ.get("GROUND_TRUTH_LOG")
    if env_path:
        paths.append(Path(env_path))
    paths.append(_REPO_ROOT / "ground-truth" / "ground_truth.log")
    paths.append(Path.cwd() / "ground_truth.log")
    paths.append(Path("/var/log/ground_truth.log"))
    # de-duplicate while preserving order
    seen: set[str] = set()
    unique: list[Path] = []
    for p in paths:
        key = str(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def load_from_file(path: Path) -> tuple[pd.DataFrame, int]:
    """Parse a JSONL ground-truth file. Returns (df, skipped_line_count)."""
    records = []
    skipped = 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(obj, dict):
                skipped += 1
                continue
            records.append(
                {
                    "timestamp": obj.get("timestamp"),
                    "anomaly_type": obj.get("anomaly_type"),
                    "detail": obj.get("detail"),
                }
            )
    df = pd.DataFrame(records, columns=GT_COLUMNS)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["timestamp"])
    return df, skipped


def load_ground_truth(
    start: Optional[datetime] = None, end: Optional[datetime] = None
) -> GroundTruthResult:
    """Load ground truth from the first available source."""
    errors: list[str] = []

    for path in candidate_paths():
        if not path.exists():
            errors.append(f"not found: {path}")
            continue
        try:
            df, skipped = load_from_file(path)
        except OSError as exc:
            errors.append(f"could not read {path}: {exc}")
            continue
        if skipped:
            errors.append(f"{path}: skipped {skipped} malformed line(s)")
        return GroundTruthResult(df=df, source=f"file: {path}", errors=errors)

    # Fallback: the ground_truth table in Postgres.
    try:
        df = queries.fetch_ground_truth_rows(start, end)
    except db.DatabaseUnavailableError as exc:
        errors.append(str(exc))
        return GroundTruthResult(df=pd.DataFrame(columns=GT_COLUMNS), source="unavailable", errors=errors)
    except db.QueryError as exc:
        errors.append(f"ground_truth table query failed: {exc}")
        return GroundTruthResult(df=pd.DataFrame(columns=GT_COLUMNS), source="unavailable", errors=errors)

    if df.empty:
        errors.append("ground_truth table is empty")
        return GroundTruthResult(df=df, source="database table (empty)", errors=errors)
    return GroundTruthResult(df=df, source="database table: ground_truth", errors=errors)
