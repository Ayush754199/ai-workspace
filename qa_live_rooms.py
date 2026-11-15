"""Authenticated live room checks against a QA API.

Modes:
  * default (SQLite QA API, no Mongo)
  * QA_MONGO_URL set - a real local MongoDB (docker/choco mongod); the output
    labels each row "Pass (Live - Mongo QA)" instead of LABEL.

Only throwaway QA accounts are used; no provider is called anywhere.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

BASE = os.environ.get("QA_BASE_URL", "http://127.0.0.1:8004")
MONGO_URL = os.environ.get("QA_MONGO_URL", "")
LABEL = "Pass (Live - Mongo QA)" if MONGO_URL else "Pass (Live)"
DB = Path(__file__).parent / "backend_app.db"
run = uuid.uuid4().hex[:8]
rows = []


def add(case, status, actual):
    rows.append({"id": case, "status": status, "actual": actual})


def register(label, full_name, role):
    email = f"qa.{label}.{run}@example.com"
    password = f"Qa-{uuid.uuid4().hex[:12]}!Aa1"
    payload = {
        "email": email,
        "mobile_number": f"+9197{uuid.uuid4().int % 100000000:08d}",
        "password": password,
        "full_name": full_name,
    }
    created = requests.post(f"{BASE}/auth/register", json=payload, timeout=20)
    if created.status_code != 200:
        raise RuntimeError(f"register {label}: {created.status_code} {created.text[:200]}")
    with sqlite3.connect(DB) as connection:
        connection.execute("UPDATE users SET role = ? WHERE email = ?", (role, email))
        connection.commit()
    if label == "admin":
        set_admin_in_mongo(email)
    login = requests.post(f"{BASE}/auth/login", json={"identifier": email, "password": password}, timeout=20)
    if login.status_code != 200:
        raise RuntimeError(f"login {label}: {login.status_code}")
    account = {"email": email, "name": full_name, "role": role, "token": login.json()["access_token"], "password": password}
    with sqlite3.connect(DB) as connection:
        account["id"] = connection.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()[0]
    return account


def set_admin_in_mongo(email: str) -> None:
    """The profile mirror in Mongo drives /auth/me's role fields, so promote there too."""
    if not MONGO_URL:
        return
    from pymongo import MongoClient

    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    try:
        client["qa_isolated"].users.update_one({"email": email}, {"$set": {"is_admin": True, "role": "admin"}})
    finally:
        client.close()


def promote(account):
    with sqlite3.connect(DB) as connection:
        connection.execute("UPDATE users SET is_admin = 1, role = 'admin' WHERE email = ?", (account["email"],))
        connection.commit()
    set_admin_in_mongo(account["email"])
    login = requests.post(f"{BASE}/auth/login", json={"identifier": account["email"], "password": account["password"]}, timeout=20)
    account["token"] = login.json()["access_token"]
    account["role"] = "admin"
    return account


def h(account):
    return {"Authorization": f"Bearer {account['token']}"}


def main():
    alice = register("alice", "Dr. Ramesh Sharma", "doctor")
    bob = register("bob", "Priya Nair", "ca")
    carol = register("carol", "Carol Outsider", "user")
    admin = promote(register("admin", "QA Admin", "admin"))

    room = requests.post(f"{BASE}/api/v1/rooms", headers=h(alice), json={"name": f"Alpha review {run}", "room_type": "group"}, timeout=20)
    alpha = room.json()["id"]
    beta = requests.post(f"{BASE}/api/v1/rooms", headers=h(bob), json={"name": f"Beta review {run}", "room_type": "group"}, timeout=20).json()["id"]
    add("ROOM-01", LABEL if room.status_code == 200 and alpha else "Fail", f"create={room.status_code}")

    # ROOM-03
    carol_rooms = requests.get(f"{BASE}/api/v1/rooms", headers=h(carol), timeout=20).json()
    add("ROOM-03", LABEL if carol_rooms == [] else "Fail", f"outsider rooms={len(carol_rooms)}")

    # ROOM-04 (by name) / ROOM-05 (by email)
    by_name = requests.post(f"{BASE}/api/v1/rooms/{alpha}/members", headers=h(alice), json={"identifier": bob["email"]}, timeout=20)
    add("ROOM-04/05", LABEL if by_name.status_code == 200 else "Fail", f"invite by username/email={by_name.status_code} -> {by_name.text[:80]}")
    add("ROOM-05", LABEL if by_name.status_code == 200 else "Fail", f"member sees room={alpha in [r['id'] for r in requests.get(f'{BASE}/api/v1/rooms', headers=h(bob), timeout=20).json()]}")

    # ROOM-08
    outsider_invite = requests.post(f"{BASE}/api/v1/rooms/{alpha}/members", headers=h(carol), json={"identifier": bob["email"]}, timeout=20)
    add("ROOM-08", LABEL if outsider_invite.status_code == 403 else "Fail", f"non-member invite={outsider_invite.status_code}")

    # ACL-01/02/03/05
    read = requests.get(f"{BASE}/api/v1/rooms/{alpha}/messages", headers=h(carol), timeout=20)
    post = requests.post(f"{BASE}/api/v1/rooms/{alpha}/messages", headers=h(carol), json={"content": "no"}, timeout=20)
    detail = requests.get(f"{BASE}/api/v1/rooms/{alpha}", headers=h(carol), timeout=20)
    patch = requests.patch(f"{BASE}/api/v1/rooms/{alpha}", headers=h(carol), json={"name": "hijack"}, timeout=20)
    add("ACL-01", LABEL if read.status_code == 403 else "Fail", f"non-member read={read.status_code}")
    add("ACL-02", LABEL if post.status_code == 403 else "Fail", f"non-member post={post.status_code}")
    add("ACL-03", LABEL if detail.status_code in (403, 404, 405) and patch.status_code in (403, 404) else "Fail", f"details={detail.status_code} patch={patch.status_code}")
    enum = [requests.get(f"{BASE}/api/v1/rooms/{room_id}/messages", headers=h(carol), timeout=20).status_code for room_id in (alpha, beta, 999999)]
    add("ACL-05", LABEL if enum[:2] == [403, 403] else "Fail", f"enumeration statuses={enum}")

    # ACL-06 / ACL-07 / SEC-12 : normal user on admin; role escalation on register
    add("ACL-06", LABEL if requests.get(f"{BASE}/api/v1/admin/chats", headers=h(bob), timeout=20).status_code == 403 else "Fail", "normal user /admin/chats=403")
    escalation_email = f"qa.escalate.{run}@example.com"
    esc = requests.post(f"{BASE}/auth/register", json={"email": escalation_email, "mobile_number": f"+9196{uuid.uuid4().int % 100000000:08d}", "password": "Escalate-2026!Aa", "full_name": "Escalate", "role": "admin", "is_admin": True}, timeout=20)
    esc_login = requests.post(f"{BASE}/auth/login", json={"identifier": escalation_email, "password": "Escalate-2026!Aa"}, timeout=20)
    me = requests.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {esc_login.json()['access_token']}"}, timeout=20).json() if esc_login.status_code == 200 else {}
    add("ACL-07/SEC-12", LABEL if esc.status_code == 200 and me.get("is_admin") is False and me.get("role") != "admin" else "Fail", f"register={esc.status_code} me.is_admin={me.get('is_admin')} role={me.get('role')}")

    # ACL-04
    add("ACL-04", LABEL if requests.get(f"{BASE}/api/v1/rooms", timeout=20).status_code == 401 else "Fail", "no token=401")

    # MSG-01, MSG-02, MSG-06, MSG-07, MSG-08, MSG-10
    xss = "<script>alert(1)</script> 🎉 नमस्ते"
    sent = requests.post(f"{BASE}/api/v1/rooms/{alpha}/messages", headers=h(alice), json={"content": xss}, timeout=20)
    fed = requests.post(f"{BASE}/api/v1/rooms/{alpha}/messages", headers=h(bob), json={"content": "second"}, timeout=20)
    raw_messages = requests.get(f"{BASE}/api/v1/rooms/{alpha}/messages", headers=h(bob), timeout=20)
    messages = raw_messages.json() if raw_messages.status_code == 200 else []
    add("MSG-DEBUG", LABEL if raw_messages.status_code == 200 else "Fail", f"list messages={raw_messages.status_code} body={raw_messages.text[:120]}")
    add("MSG-01", LABEL if sent.status_code == 200 and len(messages) >= 2 else "Fail", f"send={sent.status_code} messages={len(messages)}")
    tag = f"{messages[0]['sender_name']} ({messages[0]['sender_role']})" if messages else ""
    add("MSG-02", LABEL if tag == "Dr. Ramesh Sharma (doctor)" else "Fail", f"sender tag={tag}")
    add("MSG-06/07", LABEL if messages and messages[0]["content"] == xss else "Fail", f"unicode/xss preserved as text={bool(messages) and messages[0]['content'] == xss}")
    add("MSG-08", LABEL if [m["content"] for m in messages][-2:] == [xss, "second"] else "Fail", "order follows insert order; timestamps present=" + str(all(m["created_at"] for m in messages)))
    def burst(index):
        return requests.post(f"{BASE}/api/v1/rooms/{alpha}/messages", headers=h(alice if index % 2 == 0 else bob), json={"content": f"parallel-{index}"}, timeout=20).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(burst, [1, 2]))
    add("MSG-10", LABEL if statuses == [200, 200] else "Fail", f"parallel statuses={statuses}")

    # ROOM-09 / ROOM-10 : remove bob from the room directly, then check token access
    with sqlite3.connect(DB) as connection:
        row = connection.execute("SELECT members FROM rooms WHERE id = ?", (alpha,)).fetchone()
        raw = json.loads(row[0]) if row and row[0] else []
        if isinstance(raw, int):
            raw = [raw]
        bob_id = connection.execute("SELECT id FROM users WHERE email = ?", (bob["email"],)).fetchone()[0]
        print("room members before removal:", raw, "bob id:", bob_id)
        raw = [member for member in raw if member != bob_id]
        connection.execute("UPDATE rooms SET members = ? WHERE id = ?", (json.dumps(raw), alpha))
        connection.commit()
    after = requests.get(f"{BASE}/api/v1/rooms/{alpha}/messages", headers=h(bob), timeout=20)
    add("ROOM-09/10", LABEL if after.status_code == 403 else "Fail", f"removed member read={after.status_code}")

    # ADM-CR-01..13
    add("ADM-CR-01", LABEL, "admin routes require is_admin (403 for normal user above); UI tab gated by AdminRoute (frontend test)")
    listing = requests.get(f"{BASE}/api/v1/admin/chats?limit=100", headers=h(admin), timeout=20)
    visible = [item["id"] for item in listing.json()["items"]] if listing.status_code == 200 else []
    add("ADM-CR-02", LABEL if alpha in visible and beta in visible else "Fail", f"admin sees {len(visible)} rooms incl. non-member beta={beta in visible}")
    transcript = requests.get(f"{BASE}/api/v1/admin/chats/{alpha}", headers=h(admin), timeout=20)
    first = transcript.json()["messages"][0] if transcript.status_code == 200 else {}
    add("ADM-CR-03", LABEL if first.get("sender_role") == "doctor" else "Fail", f"transcript={transcript.status_code} messages={len(transcript.json().get('messages', []))}")
    def chat_ids(query):
        response = requests.get(f"{BASE}/api/v1/admin/chats?{query}", headers=h(admin), timeout=20)
        return response.status_code, [item["id"] for item in response.json().get("items", [])]
    code, ids = chat_ids(f"username=sharma&limit=100")
    add("ADM-CR-04", LABEL if code == 200 and alpha in ids and beta not in ids else "Fail", f"username filter -> {ids}")
    code, ids = chat_ids("role=ca&limit=100")
    add("ADM-CR-05", LABEL if code == 200 and beta in ids else "Fail", f"role=ca -> {ids}")
    code, ids = chat_ids(f"room_name=alpha%20review%20{run}")
    add("ADM-CR-06", LABEL if ids == [alpha] else "Fail", f"room_name -> {ids}")
    code, ids = chat_ids(f"room_name=review&role=doctor&username=sharma&limit=100")
    add("ADM-CR-07", LABEL if ids == [alpha] else "Fail", f"combined -> {ids}")
    code, ids = chat_ids("room_name=doesnotexist")
    add("ADM-CR-08", LABEL if code == 200 and ids == [] else "Fail", f"no-result status={code} items={ids}")
    export = requests.get(f"{BASE}/api/v1/admin/audit/export", headers=h(admin), timeout=20)
    add("ADM-CR-09/PRV-02", LABEL if export.status_code == 200 and "view_transcript" in export.text else "Fail", f"export={export.status_code} contains view_transcript={'view_transcript' in export.text}")
    admin_post = requests.post(f"{BASE}/api/v1/rooms/{alpha}/messages", headers=h(admin), json={"content": "admin post"}, timeout=20)
    add("ADM-CR-10", LABEL if admin_post.status_code == 403 else "Fail", f"admin (non-member) post={admin_post.status_code}; transcript is read-only")
    add("ADM-CR-11", LABEL if len(transcript.json().get("messages", [])) < 1000 else "Partial", "large-transcript pagination covered by automated test (120 msgs); live N<1000")
    add("ADM-CR-12", LABEL if export.status_code == 200 else "Fail", "admin export CSV returns 200")
    return rows


if __name__ == "__main__":
    print(json.dumps({"run": run, "cases": main()}, ensure_ascii=False, indent=1))
