"""Live DB-failure probe: registration must fail fast with 503, not hang."""
import sqlite3
import time
import uuid

import requests

BASE = "http://127.0.0.1:8004"
email = f"qa.dbfail.{uuid.uuid4().hex[:8]}@example.com"
payload = {"email": email, "mobile_number": f"+9195{uuid.uuid4().int % 100000000:08d}", "password": "DbFail-2026!Aa", "full_name": "DB Fail QA"}
started = time.monotonic()
response = requests.post(f"{BASE}/auth/register", json=payload, timeout=60)
elapsed = round(time.monotonic() - started, 2)
print("register", response.status_code, elapsed, "s", response.text[:160])
connection = sqlite3.connect("backend_app.db")
print("row created:", connection.execute("SELECT count(*) FROM users WHERE email = ?", (email,)).fetchone()[0])
