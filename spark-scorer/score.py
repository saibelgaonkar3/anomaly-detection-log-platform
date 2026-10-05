"""
Day 5-6: MapReduce-based anomaly scoring job

(Unit 3: Introduction to Map Reduce)

Runs as a repeating batch job (every SCORE_INTERVAL_SECONDS):

1. Pull unscored rows from Postgres `logs` table.

2. MAP phase 1:
   Each row -> ((service, endpoint, time_window),
                (count, latency, latency^2, is_error))

3. REDUCE phase:
   Reduce by (service, endpoint, time_window) to calculate:
   - count
   - sum latency
   - sum squared latency
   - error count

4. MAP phase 2:
   Re-map every original row against its time-window statistics to
   calculate:
   - latency z-score
   - error-spike detection

5. Write anomaly_score / is_anomaly back to Postgres.

The explicit RDD map / reduceByKey stages make the MapReduce
processing visible for the Cloud Computing syllabus.
"""

import math
import time

import psycopg2
from pyspark.sql import SparkSession


DB_CONFIG = {
    "host": "postgres",
    "port": 5432,
    "dbname": "logs_db",
    "user": "loguser",
    "password": "logpass",
}

JDBC_URL = (
    f"jdbc:postgresql://"
    f"{DB_CONFIG['host']}:{DB_CONFIG['port']}/"
    f"{DB_CONFIG['dbname']}"
)

JDBC_PROPS = {
    "user": DB_CONFIG["user"],
    "password": DB_CONFIG["password"],
    "driver": "org.postgresql.Driver",
}

SCORE_INTERVAL_SECONDS = 30
Z_SCORE_THRESHOLD = 3.0
BATCH_ERROR_RATE_THRESHOLD = 0.10
MIN_WINDOW_REQUESTS = 20
MIN_WINDOW_ERRORS = 3

# Logs are grouped into 10-second windows for error-spike detection.
TIME_WINDOW_SECONDS = 10


def get_spark():
    return (
        SparkSession.builder
        .appName("anomaly-mapreduce-scorer")
        .master("local[1]")
        .config("spark.driver.memory", "512m")
        .config("spark.jars", "/app/postgresql-42.7.3.jar")
        .getOrCreate()
    )


def fetch_unscored_batch(spark):
    """Read rows that haven't been scored yet."""
    df = (
        spark.read.jdbc(
            url=JDBC_URL,
            table=(
                "(SELECT id, timestamp, service, endpoint, "
                "latency_ms, status_code "
                "FROM logs "
                "WHERE anomaly_score IS NULL) AS unscored"
            ),
            properties=JDBC_PROPS,
        )
    )
    return df


def get_time_window(timestamp):
    """
    Convert a timestamp into the start of its 10-second window.

    Example:
        12:30:01 -> 12:30:00
        12:30:07 -> 12:30:00
        12:30:13 -> 12:30:10
    """
    return (
        int(timestamp.timestamp() // TIME_WINDOW_SECONDS)
        * TIME_WINDOW_SECONDS
    )


def map_phase_1(row):
    """
    MAP phase 1.

    Group logs by:
        (service, endpoint, time_window)

    Each row produces:
        key -> (count, sum_latency, sum_latency_squared, error_count)
    """
    timestamp = row["timestamp"]

    window_start = get_time_window(timestamp)

    key = (
        row["service"],
        row["endpoint"],
        window_start,
    )

    latency = (
        row["latency_ms"]
        if row["latency_ms"] is not None
        else 0.0
    )

    is_error = (
        1
        if (row["status_code"] or 0) >= 400
        else 0
    )

    value = (
        1,
        latency,
        latency * latency,
        is_error,
    )

    return (key, value)


def reduce_phase(a, b):
    """
    REDUCE phase.

    Combine two partial aggregates for the same
    (service, endpoint, time_window) key.
    """
    return (
        a[0] + b[0],  # count
        a[1] + b[1],  # sum_latency
        a[2] + b[2],  # sum_latency_squared
        a[3] + b[3],  # error_count
    )


def stats_from_aggregate(agg):
    """
    Convert the reduced aggregate into useful statistics.

    Returns:
        mean_latency
        stddev_latency
        error_rate
        request_count
    """
    count, sum_lat, sum_sq_lat, error_count = agg

    mean = (
        sum_lat / count
        if count
        else 0.0
    )

    variance = (
        max(
            (sum_sq_lat / count) - (mean * mean),
            0.0,
        )
        if count
        else 0.0
    )

    stddev = math.sqrt(variance)

    error_rate = (
        error_count / count
        if count
        else 0.0
    )

    return mean, stddev, error_rate, count


def map_phase_2(row, stats_broadcast):
    """
    MAP phase 2.

    Re-visit every original row and compare it against
    the statistics for its service/endpoint/time window.

    An individual request is considered anomalous when:

    1. Its latency z-score exceeds the threshold, OR
    2. It is an error AND the error rate in its 10-second
       window exceeds the configured error-rate threshold.
    """
    timestamp = row["timestamp"]

    window_start = get_time_window(timestamp)

    key = (
        row["service"],
        row["endpoint"],
        window_start,
    )

    mean, stddev, error_rate, count = (
        stats_broadcast.value.get(
            key,
            (0.0, 0.0, 0.0, 0),
        )
    )

    latency = (
        row["latency_ms"]
        if row["latency_ms"] is not None
        else 0.0
    )

    if stddev > 0:
        z = (latency - mean) / stddev
    else:
        z = 0.0

    anomaly_score = round(abs(z), 4)

    # Determine whether this individual request is an error.
    is_error = (
        (row["status_code"] or 0) >= 400
    )

    # Detect an error spike in this 10-second window.
    #
    # Importantly, only the error requests themselves are
    # flagged. Successful requests in the same window are
    # not automatically marked anomalous.
    error_count = error_rate * count

    error_spike = (
        count >= MIN_WINDOW_REQUESTS
        and error_count >= MIN_WINDOW_ERRORS
        and error_rate > BATCH_ERROR_RATE_THRESHOLD
        and is_error
    )

    # Detect latency anomalies independently.
    latency_anomaly = (
        abs(z) > Z_SCORE_THRESHOLD
    )

    is_anomaly = bool(
        latency_anomaly
        or error_spike
    )

    return (
        row["id"],
        anomaly_score,
        is_anomaly,
    )


def write_scores_back(scored_rows):
    """Batch UPDATE Postgres with computed anomaly scores."""
    if not scored_rows:
        return

    conn = psycopg2.connect(**DB_CONFIG)

    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                UPDATE logs
                SET anomaly_score = %s,
                    is_anomaly = %s
                WHERE id = %s
                """,
                [
                    (
                        score,
                        flag,
                        row_id,
                    )
                    for (
                        row_id,
                        score,
                        flag,
                    ) in scored_rows
                ],
            )

        conn.commit()

        print(
            f"[scorer] wrote "
            f"{len(scored_rows)} scored rows",
            flush=True,
        )

    except Exception as e:
        print(
            f"[scorer] write failed: {e}",
            flush=True,
        )
        conn.rollback()

    finally:
        conn.close()


def run_batch(spark):
    """Run one MapReduce anomaly-scoring batch."""

    df = fetch_unscored_batch(spark)

    count = df.count()

    if count == 0:
        print(
            "[scorer] no unscored rows, skipping batch",
            flush=True,
        )
        return

    print(
        f"[scorer] scoring {count} rows",
        flush=True,
    )

    # Collect the current batch.
    # This is acceptable for the current local[*] prototype
    # where batch sizes are intentionally small.
    rows = df.collect()

    rdd = spark.sparkContext.parallelize(rows)

    # ---------------------------------------------------------
    # MAP PHASE 1 + REDUCE PHASE
    # ---------------------------------------------------------
    aggregated = (
        rdd
        .map(map_phase_1)
        .reduceByKey(reduce_phase)
        .collectAsMap()
    )

    # Convert aggregates into statistics.
    stats = {
        key: stats_from_aggregate(agg)
        for key, agg in aggregated.items()
    }

    # Broadcast statistics to the workers.
    stats_broadcast = (
        spark.sparkContext.broadcast(stats)
    )

    # ---------------------------------------------------------
    # MAP PHASE 2
    # ---------------------------------------------------------
    scored = (
        rdd
        .map(
            lambda r: map_phase_2(
                r,
                stats_broadcast,
            )
        )
        .collect()
    )

    # ---------------------------------------------------------
    # WRITE RESULTS
    # ---------------------------------------------------------
    write_scores_back(scored)


def main():
    spark = get_spark()

    spark.sparkContext.setLogLevel("WARN")

    print(
        "[scorer] started, running every "
        f"{SCORE_INTERVAL_SECONDS}s",
        flush=True,
    )

    while True:
        try:
            run_batch(spark)

        except Exception as e:
            print(
                f"[scorer] batch error: {e}",
                flush=True,
            )

        time.sleep(
            SCORE_INTERVAL_SECONDS
        )


if __name__ == "__main__":
    main()