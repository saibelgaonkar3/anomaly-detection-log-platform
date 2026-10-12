#  Log Anomaly Detection Platform — Frontend

A Streamlit dashboard for the Cloud Computing PBL anomaly-detection
pipeline. It reads **live data from the existing PostgreSQL database**
and the **actual Spark scorer output** — every metric on screen is
computed from real rows, nothing is hardcoded.

```
Docker stack (backend, unchanged)          This frontend (host)
┌────────────────────────────────┐        ┌──────────────────────────┐
│ FastAPI services → docker logs │        │ streamlit run            │
│ log-shipper → PostgreSQL :5432 │ ─────▶ │ frontend/streamlit_app.py│
│ spark-scorer → anomaly_score   │  :5432 │ (5 pages, Plotly + Pandas)│
└────────────────────────────────┘        └──────────────────────────┘
```

## Pages

| Page | What it shows |
|------|---------------|
| **Overview** | Total requests, anomaly count & rate, avg/p95 latency, error rate; traffic, latency and anomaly timelines |
| **Log Explorer** | Searchable log table with filters for time range, service, endpoint, status class, anomaly status and free text; paginated, CSV export |
| **Anomaly Investigation** | Flagged requests ranked by anomaly score, score-vs-time scatter, per-service breakdown, expandable request details |
| **Evaluation** | Confusion matrix, precision / recall / F1 against the traffic-generator's ground truth (`error_spike` & `latency_spike`; `suspicious_sequence` excluded — not implemented in the scorer) |
| **Service Health** | Per-microservice volume, avg/p95 latency, error rate, anomaly count + comparison charts |

## Dependencies

Python **3.10–3.13** (3.13 recommended) plus:

```
streamlit>=1.41
plotly>=5.24
pandas>=2.2
psycopg[binary]>=3.2     # PostgreSQL driver (psycopg2-binary also works)
```

Dev/test only: `pytest>=8.0` (see `requirements-dev.txt`).

No other tooling is required — Docker, Node, etc. are **not** needed to
run the dashboard itself (only to produce the data it displays).

## Local startup

```powershell
# 1. From the repository root, create a virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1          # Windows
# source venv/bin/activate           # macOS / Linux

# 2. Install dependencies
pip install -r frontend/requirements.txt

# 3. Run the dashboard
streamlit run frontend/streamlit_app.py
```

Open <http://localhost:8501>.

### Getting data to display

The dashboard reads PostgreSQL directly. With the backend stack running
(`docker compose up -d` from the repo root) it connects to the
host-mapped port automatically:

| Env var | Default | Meaning |
|---------|---------|---------|
| `DB_HOST` | `localhost` | Postgres host |
| `DB_PORT` | `5432` | Postgres port (compose maps 5432) |
| `DB_NAME` | `logs_db` | Database |
| `DB_USER` | `loguser` | User |
| `DB_PASSWORD` | `logpass` | Password |
| `DB_CONNECT_TIMEOUT` | `5` | Connect timeout (s) |
| `DASHBOARD_CACHE_TTL` | `30` | Query cache TTL (s) |
| `GROUND_TRUTH_LOG` | *(unset)* | Explicit ground-truth file path |

If the database is unreachable every page shows a friendly warning and
the sidebar's **Postgres connection** panel explains how to fix it — the
UI never crashes.

### Ground-truth resolution (Evaluation page)

1. `GROUND_TRUTH_LOG` environment variable
2. `<repo>/ground-truth/ground_truth.log` (the compose bind mount of the
   traffic-generator's `/var/log`)
3. `./ground_truth.log` relative to the working directory
4. `/var/log/ground_truth.log`
5. Fallback: the `ground_truth` **table** in PostgreSQL

## Tests

Pure-logic tests (transforms, evaluation matching, ground-truth parsing)
run without a database or Docker:

```powershell
pip install -r frontend/requirements-dev.txt
pytest frontend/tests -q
```

## Design notes & limitations

- **Anomaly rate** is computed over *scored* rows (`anomaly_score IS NOT
  NULL`); the Spark job runs every ~30s, so the newest rows may briefly
  be unscored (shown as a caption on Overview).
- **Evaluation is event-level and window-based**: ground truth records
  *when* an anomaly was injected, not which log rows caused it, so
  matching clusters of flagged rows to injection windows is
  approximate. `error_spike` events allow a 25s window, `latency_spike`
  20s, plus a 5s look-back for scorer batch delay.
- `suspicious_sequence` events are deliberately excluded from metrics —
  the current Spark scorer has no sequence/session detection.
- All SQL is parameterized and pushed down to PostgreSQL; result sets
  are cached with `st.cache_data` (TTL configurable) and the sidebar has
  a manual **Refresh** button.
- The frontend is read-only: it never writes to the database and does
  not modify any backend service, Docker config, or schema.
