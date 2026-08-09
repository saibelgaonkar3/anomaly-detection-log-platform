import time
import random
import json
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, Request

app = FastAPI()
SERVICE_NAME = "payments-service"

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

@app.post("/charge")
def charge(request: Request):
    request_id = str(uuid.uuid4())
    start = time.time()
    time.sleep(random.uniform(0.05, 0.2))

    if random.random() < 0.01:
        status_code, message = 500, "payment gateway timeout"
    else:
        status_code, message = 200, "charge successful"

    latency_ms = (time.time() - start) * 1000
    log_event("/charge", latency_ms, status_code, message, request_id)
    return {"status": status_code, "request_id": request_id}

@app.post("/refund")
def refund(request: Request):
    request_id = str(uuid.uuid4())
    start = time.time()
    time.sleep(random.uniform(0.03, 0.1))
    status_code, message = 200, "refund processed"
    latency_ms = (time.time() - start) * 1000
    log_event("/refund", latency_ms, status_code, message, request_id)
    return {"status": status_code, "request_id": request_id}

@app.get("/health")
def health():
    return {"status": "ok"}