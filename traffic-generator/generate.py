import time
import random
import json
import requests
import psycopg2
from datetime import datetime, timezone

SERVICES = {
    "auth": "http://auth-service:8000",
    "orders": "http://orders-service:8000",
    "payments": "http://payments-service:8000",
    "inventory": "http://inventory-service:8000",
}

GROUND_TRUTH_LOG = "/var/log/ground_truth.log"
DB_CONFIG = {
    "host": "postgres",
    "port": 5432,
    "dbname": "logs_db",
    "user": "loguser",
    "password": "logpass",
    "connect_timeout": 5,
}

def log_anomaly(anomaly_type: str, detail: str):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "anomaly_type": anomaly_type,
        "detail": detail,
    }
    with open(GROUND_TRUTH_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ground_truth (timestamp, anomaly_type, detail) VALUES (%s, %s, %s)",
                (entry["timestamp"], anomaly_type, detail),
            )
        conn.close()
    except Exception as e:
        print(f"[GROUND TRUTH] DB insert failed: {e}", flush=True)
    print(f"[GROUND TRUTH] {entry}", flush=True)

def normal_traffic_tick():
    calls = [
        (SERVICES["auth"] + "/login", "post", 0.35),
        (SERVICES["auth"] + "/verify", "post", 0.25),
        (SERVICES["orders"] + "/create", "post", 0.15),
        (SERVICES["orders"] + "/status", "post", 0.10),
        (SERVICES["inventory"] + "/check", "get", 0.10),
        (SERVICES["payments"] + "/charge", "post", 0.05),
    ]
    url, method, _ = random.choices(calls, weights=[c[2] for c in calls])[0]
    try:
        if method == "post":
            requests.post(url, timeout=8)
        else:
            requests.get(url, timeout=8)
    except requests.RequestException:
        pass

def inject_error_spike():
    log_anomaly("error_spike", "hammering payments-service /charge for 25s")
    end_time = time.time() + 25
    while time.time() < end_time:
        try:
            requests.post(SERVICES["payments"] + "/charge", timeout=3)
        except requests.RequestException:
            pass
        time.sleep(0.05)

def inject_latency_spike():
    log_anomaly("latency_spike", "inventory-service /check slow_mode for 20s")
    end_time = time.time() + 20
    while time.time() < end_time:
        try:
            requests.get(SERVICES["inventory"] + "/check", params={"slow_mode": True}, timeout=8)
        except requests.RequestException:
            pass
        time.sleep(0.5)

def inject_suspicious_sequence():
    log_anomaly("suspicious_sequence", "repeated failed logins then a charge (credential-stuffing pattern)")
    for _ in range(15):
        try:
            requests.post(SERVICES["auth"] + "/login", timeout=3)
        except requests.RequestException:
            pass
        time.sleep(0.1)
    try:
        requests.post(SERVICES["payments"] + "/charge", timeout=3)
    except requests.RequestException:
        pass

ANOMALIES = [inject_error_spike, inject_latency_spike, inject_suspicious_sequence]

def main():
    last_anomaly_time = time.time()
    ANOMALY_INTERVAL = 180

    while True:
        normal_traffic_tick()
        time.sleep(random.uniform(0.1, 0.4))

        if time.time() - last_anomaly_time > ANOMALY_INTERVAL:
            anomaly_fn = random.choice(ANOMALIES)
            anomaly_fn()
            last_anomaly_time = time.time()

if __name__ == "__main__":
    main()