"""Create (or reuse) a QA admin account for the 360px surveillance-tab measurement."""
import sqlite3
import uuid

import requests

QA = "http://127.0.0.1:8004"
email = "qa.mobile.admin@example.com"
password = "Mobile-QA-2026!Aa"

created = requests.post(f"{QA}/auth/register", json={
    "email": email,
    "mobile_number": "+919200000001",
    "password": password,
    "full_name": "Mobile QA Admin",
}, timeout=20)

connection = sqlite3.connect("backend_app.db")
connection.execute("UPDATE users SET is_admin = 1, role = 'admin' WHERE email = ?", (email,))
# Give the account one room with a message so the audit tab has content.
connection.commit()
row = connection.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
connection.close()

login = requests.post(f"{QA}/auth/login", json={"identifier": email, "password": password}, timeout=20)
print("register", created.status_code, "login", login.status_code, "user_id", row[0] if row else None)
if login.status_code == 200:
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    room = requests.post(f"{QA}/api/v1/rooms", headers=headers, json={"name": "Mobile Elevation Check", "room_type": "group"}, timeout=20)
    if room.status_code == 200:
        requests.post(f"{QA}/api/v1/rooms/{room.json()['id']}/messages", headers=headers,
                      json={"content": "Mobile viewport check message " + uuid.uuid4().hex[:4]}, timeout=20)
    print("admin chats", requests.get(f"{QA}/api/v1/admin/chats", headers=headers, timeout=20).status_code)
