import docker
import psycopg2
import json
import threading
import time

DB_CONFIG = {
    "host": "postgres",
    "port": 5432,
    "dbname": "logs_db",
    "user": "loguser",
    "password": "logpass",
}

SERVICE_CONTAINERS = [
    "auth-service",
    "orders-service",
    "payments-service",
    "inventory-service",
]

def get_connection():
    while True:
        try:
            return psycopg2.connect(**DB_CONFIG)
        except psycopg2.OperationalError:
            print("Postgres not ready yet, retrying in 2s...", flush=True)
            time.sleep(2)

def insert_log(conn, entry: dict):
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO logs (timestamp, service, level, endpoint, latency_ms, status_code, message, request_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    entry.get("timestamp"),
                    entry.get("service"),
                    entry.get("level"),
                    entry.get("endpoint"),
                    entry.get("latency_ms"),
                    entry.get("status_code"),
                    entry.get("message"),
                    entry.get("request_id"),
                ),
            )
        conn.commit()
    except Exception as e:
        print(f"Insert failed: {e} | entry={entry}", flush=True)
        conn.rollback()

def tail_container(client, container_name: str, conn):
    """Stream logs from one container and insert parsed JSON lines."""
    while True:
        try:
            container = client.containers.get(container_name)
        except docker.errors.NotFound:
            print(f"Container {container_name} not found yet, retrying...", flush=True)
            time.sleep(2)
            continue

        try:
            for line in container.logs(stream=True, follow=True, since=int(time.time())):
                raw = line.decode("utf-8").strip()
                try:
                    entry = json.loads(raw)
                    insert_log(conn, entry)
                except json.JSONDecodeError:
                    continue  # skip non-JSON lines (e.g. uvicorn startup messages)
        except Exception as e:
            print(f"Stream error on {container_name}: {e}, reconnecting...", flush=True)
            time.sleep(2)

def main():
    client = docker.from_env()
    conn = get_connection()
    print("Log shipper connected to Postgres, starting tailers...", flush=True)

    threads = []
    for name in SERVICE_CONTAINERS:
        # each container needs its OWN connection - psycopg2 conns aren't thread-safe
        t_conn = get_connection()
        t = threading.Thread(target=tail_container, args=(client, name, t_conn), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

if __name__ == "__main__":
    main()