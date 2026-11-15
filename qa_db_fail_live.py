"""Start the API against an unreachable Mongo host and time registration.

Confirms the fail-fast path works on a real server process, not only in tests.
"""
import contextlib
import os
import socket
import subprocess
import sys
import time
import uuid

import requests

PY = os.path.join(".venv", "Scripts", "python.exe")
MONGODB_URL = os.environ["FAIL_MONGODB_URL"]
env = {**os.environ, "PYTHONPATH": "backend", "MONGODB_URL": MONGODB_URL, "MONGODB_DATABASE": "qa_unreachable", "JWT_SECRET": "qa-only-jwt-secret-0123456789abcdef", "AI_GATEWAY_MODE": "live", "API_DOCS_ENABLED": "false", "AUTH_RATE_LIMIT_PER_MINUTE": "1000"}


def port_open(port):
    with contextlib.closing(socket.socket()) as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


log = open("qa_logs/qa_api_8005.log", "w", encoding="utf-8")
server = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8005"], env=env, stdout=log, stderr=subprocess.STDOUT)
try:
    for _ in range(60):
        if port_open(8005):
            break
        time.sleep(0.5)
    payload = {"email": f"qa.unreachable.{uuid.uuid4().hex[:8]}@example.com", "mobile_number": f"+9194{uuid.uuid4().int % 100000000:08d}", "password": "Unreachable-2026!Aa", "full_name": "Unreachable DB"}
    started = time.monotonic()
    try:
        response = requests.post("http://127.0.0.1:8005/auth/register", json=payload, timeout=90)
        print("register", response.status_code, round(time.monotonic() - started, 2), "s", response.text[:160])
    except requests.Timeout:
        print("register TIMED OUT after", round(time.monotonic() - started, 2), "s (hang reproduced)")
    started = time.monotonic()
    try:
        response = requests.post("http://127.0.0.1:8005/auth/login", json={"identifier": "x@example.com", "password": "x"}, timeout=90)
        print("login", response.status_code, round(time.monotonic() - started, 2), "s")
    except requests.Timeout:
        print("login TIMED OUT after", round(time.monotonic() - started, 2), "s")
finally:
    server.terminate()
    with contextlib.suppress(Exception):
        server.wait(timeout=15)
    log.close()
