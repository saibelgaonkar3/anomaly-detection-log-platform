import time
import random
import json
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, Request

app = FastAPI()
SERVICE_NAME = "inventory-service"

def log_event(endpoint: str, latency_ms: float, status_code: int, message: str, request_id: str):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": SERVICE_NAME,
        "level": "ERROR" if status_code >= 400 else "INFO",
        "endpoint": endpoint,
        "latency_ms": round(latency_ms, 2),
        "status_code": status_code,
        "message": message,
        "request_id": request_id,
    }
    print(json.dumps(entry), flush=True)

@app.get("/check")
def check(item_id: str = "sku_1", slow_mode: bool = False):
    request_id = str(uuid.uuid4())
    start = time.time()

    if slow_mode:
        time.sleep(random.uniform(2, 5))   # injected latency anomaly
    else:
        time.sleep(random.uniform(0.01, 0.04))

    status_code, message = 200, "in stock"
    latency_ms = (time.time() - start) * 1000
    log_event("/check", latency_ms, status_code, message, request_id)
    return {"status": status_code, "request_id": request_id}

@app.post("/reserve")
def reserve(request: Request):
    request_id = str(uuid.uuid4())
    start = time.time()
    time.sleep(random.uniform(0.02, 0.08))
    status_code, message = 200, "item reserved"
    latency_ms = (time.time() - start) * 1000
    log_event("/reserve", latency_ms, status_code, message, request_id)
    return {"status": status_code, "request_id": request_id}

@app.get("/health")
def health():
    return {"status": "ok"}