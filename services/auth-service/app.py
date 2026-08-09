import time
import random
import json
import sys
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, Request

app = FastAPI()
SERVICE_NAME = "auth-service"

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

@app.post("/login")
def login(request: Request):
    request_id = str(uuid.uuid4())
    start = time.time()

    time.sleep(random.uniform(0.01, 0.1))

    if random.random() < 0.02:
        status_code, message = 401, "invalid credentials"
    else:
        status_code, message = 200, "login successful"

    latency_ms = (time.time() - start) * 1000
    log_event("/login", latency_ms, status_code, message, request_id)
    return {"status": status_code, "request_id": request_id}

@app.post("/verify")
def verify(request: Request):
    request_id = str(uuid.uuid4())
    start = time.time()
    time.sleep(random.uniform(0.005, 0.05))
    status_code, message = 200, "token valid"
    latency_ms = (time.time() - start) * 1000
    log_event("/verify", latency_ms, status_code, message, request_id)
    return {"status": status_code, "request_id": request_id}

@app.post("/logout")
def logout(request: Request):
    request_id = str(uuid.uuid4())
    start = time.time()
    time.sleep(random.uniform(0.005, 0.03))
    status_code, message = 200, "logged out"
    latency_ms = (time.time() - start) * 1000
    log_event("/logout", latency_ms, status_code, message, request_id)
    return {"status": status_code, "request_id": request_id}

@app.get("/health")
def health():
    return {"status": "ok"}