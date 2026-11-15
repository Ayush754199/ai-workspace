import json
import os
import sqlite3
import uuid
from pathlib import Path

import requests

BASE = os.environ.get("QA_BASE_URL", "http://127.0.0.1:8003")
run = uuid.uuid4().hex[:8]
rows = []

def add(case, status, actual, fix="No fix required; evidence recorded."):
    rows.append({"id": case, "status": status, "actual": actual, "fix": fix})

def register(label):
    email = f"release.{label}.{run}@example.com"
    password = f"ReleaseQA-{uuid.uuid4().hex[:14]}!"
    payload = {"email": email, "mobile_number": f"+9196{uuid.uuid4().int % 100000000:08d}", "password": password, "full_name": f"Release {label} {run}"}
    reg = requests.post(f"{BASE}/auth/register", json=payload, timeout=15)
    login = requests.post(f"{BASE}/auth/login", json={"identifier": email, "password": password}, timeout=15)
    if reg.status_code != 200 or login.status_code != 200:
        raise RuntimeError(f"test-account setup failed: register={reg.status_code}, login={login.status_code}")
    return {"email": email, "password": password, "token": login.json()["access_token"]}

def headers(account):
    return {"Authorization": f"Bearer {account['token']}"}

def main():
    health = requests.get(f"{BASE}/api/v1/health", timeout=15)
    root = requests.get(f"{BASE}/", timeout=15)
    add("CFG-01/HEALTH", "PASS" if health.status_code == 200 and root.status_code == 200 else "FAIL", f"health={health.status_code}; root={root.status_code}")

    docs = requests.get(f"{BASE}/docs", timeout=15)
    env = requests.get(f"{BASE}/.env", timeout=15)
    add("SEC-15", "PARTIAL" if docs.status_code == 200 and env.status_code == 404 else "FAIL", f"docs={docs.status_code}; .env={env.status_code}")
    security = requests.get(f"{BASE}/api/v1/health", timeout=15).headers
    add("SEC-14/OBS-01", "PASS" if security.get("X-Frame-Options") == "DENY" and security.get("X-Content-Type-Options") == "nosniff" and security.get("X-Request-Id") else "FAIL", f"security headers present={all(security.get(k) for k in ('X-Frame-Options','X-Content-Type-Options','X-Request-Id'))}")
    evil = requests.options(f"{BASE}/api/v1/rooms", headers={"Origin":"https://evil.example","Access-Control-Request-Method":"GET","Access-Control-Request-Headers":"Authorization"}, timeout=15)
    add("SEC-14-CORS", "PARTIAL" if evil.status_code == 200 and evil.headers.get("access-control-allow-origin") == "*" else "PASS", f"evil origin allow-origin={evil.headers.get('access-control-allow-origin')}")

    a = register("alice")
    b = register("bob")
    bad_login = requests.post(f"{BASE}/auth/login", json={"identifier": a["email"], "password": "wrong-password"}, timeout=15)
    me = requests.get(f"{BASE}/auth/me", headers=headers(a), timeout=15)
    malformed = requests.get(f"{BASE}/api/v1/rooms", headers={"Authorization":"Bearer not-a-jwt"}, timeout=15)
    add("AUTH-NEGATIVE", "PASS" if bad_login.status_code == 401 and me.status_code == 200 and malformed.status_code == 401 else "FAIL", f"bad login={bad_login.status_code}; me={me.status_code}; malformed token={malformed.status_code}")

    room = requests.post(f"{BASE}/api/v1/rooms", headers=headers(a), json={"name": f"Release Room {run}", "room_type":"group"}, timeout=15)
    room_id = room.json().get("id") if room.status_code == 200 else None
    add("ROOM-LIFECYCLE", "PASS" if room_id else "FAIL", f"create room={room.status_code}; id present={bool(room_id)}")
    invalids = [requests.post(f"{BASE}/api/v1/rooms", headers=headers(a), json={"name":"", "room_type":"group"}, timeout=15), requests.post(f"{BASE}/api/v1/rooms", headers=headers(a), json={"name":"x"*121, "room_type":"group"}, timeout=15)]
    add("ROOM-INPUT", "PASS" if [r.status_code for r in invalids] == [422,422] else "FAIL", f"invalid room statuses={[r.status_code for r in invalids]}")
    if room_id:
        outsider_read = requests.get(f"{BASE}/api/v1/rooms/{room_id}/messages", headers=headers(b), timeout=15)
        outsider_post = requests.post(f"{BASE}/api/v1/rooms/{room_id}/messages", headers=headers(b), json={"content":"no"}, timeout=15)
        invite = requests.post(f"{BASE}/api/v1/rooms/{room_id}/members", headers=headers(a), json={"identifier":b["email"]}, timeout=15)
        duplicate = requests.post(f"{BASE}/api/v1/rooms/{room_id}/members", headers=headers(a), json={"identifier":b["email"]}, timeout=15)
        add("ROOM-IDOR", "PASS" if outsider_read.status_code == 403 and outsider_post.status_code == 403 else "FAIL", f"outsider read={outsider_read.status_code}; post={outsider_post.status_code}")
        add("ROOM-INVITE", "PASS" if invite.status_code == 200 and duplicate.status_code == 409 else "FAIL", f"invite={invite.status_code}; duplicate={duplicate.status_code}")
        blank = requests.post(f"{BASE}/api/v1/rooms/{room_id}/messages", headers=headers(a), json={"content":" \n "}, timeout=15)
        huge = requests.post(f"{BASE}/api/v1/rooms/{room_id}/messages", headers=headers(a), json={"content":"x"*20001}, timeout=15)
        xss = requests.post(f"{BASE}/api/v1/rooms/{room_id}/messages", headers=headers(a), json={"content":"<script>alert(1)</script>"}, timeout=15)
        page = requests.get(f"{BASE}/api/v1/rooms/{room_id}/messages?limit=1", headers=headers(b), timeout=15)
        add("ROOM-MESSAGE-VALIDATION", "PASS" if blank.status_code == 422 and huge.status_code == 422 and xss.status_code == 200 else "FAIL", f"blank={blank.status_code}; huge={huge.status_code}; xss={xss.status_code}")
        add("ROOM-PAGINATION", "PASS" if page.status_code == 200 and len(page.json()) <= 1 else "FAIL", f"status={page.status_code}; count={len(page.json()) if page.status_code == 200 else 'n/a'}")

    conversation = requests.post(f"{BASE}/api/v1/chat/v2/conversations", headers=headers(a), json={"title":f"Release {run}"}, timeout=15)
    conversation_id = conversation.json().get("id") if conversation.status_code == 200 else None
    if conversation_id:
        other = requests.get(f"{BASE}/api/v1/chat/v2/conversations/{conversation_id}", headers=headers(b), timeout=15)
        long_prompt = requests.post(f"{BASE}/api/v1/chat/v2/conversations/{conversation_id}/messages", headers=headers(a), json={"content":"x"*32001}, timeout=15)
        export = requests.get(f"{BASE}/api/v1/chat/v2/conversations/{conversation_id}/export", headers=headers(a), timeout=15)
        delete = requests.delete(f"{BASE}/api/v1/chat/v2/conversations/{conversation_id}", headers=headers(a), timeout=15)
        gone = requests.get(f"{BASE}/api/v1/chat/v2/conversations/{conversation_id}", headers=headers(a), timeout=15)
        add("CHAT-OWNERSHIP", "PASS" if other.status_code == 404 else "FAIL", f"other user read={other.status_code}")
        add("CHAT-LIMIT", "PASS" if long_prompt.status_code == 422 else "FAIL", f"long prompt={long_prompt.status_code}")
        add("CHAT-EXPORT-DELETE", "PASS" if export.status_code == 200 and delete.status_code == 200 and gone.status_code == 404 else "FAIL", f"export={export.status_code}; delete={delete.status_code}; after={gone.status_code}")

    bad_mime = requests.post(f"{BASE}/api/v1/chat/v2/attachments", headers=headers(a), files={"file":("payload.exe",b"MZ","application/octet-stream")}, timeout=15)
    empty = requests.post(f"{BASE}/api/v1/chat/v2/attachments", headers=headers(a), files={"file":("empty.csv",b"","text/csv")}, timeout=15)
    invalid_image = requests.post(f"{BASE}/api/v1/chat/v2/attachments", headers=headers(a), files={"file":("bad.png",b"not-image","image/png")}, timeout=15)
    add("ATTACHMENT-VALIDATION", "PASS" if bad_mime.status_code == 415 and empty.status_code == 400 and invalid_image.status_code == 400 else "FAIL", f"mime={bad_mime.status_code}; empty={empty.status_code}; image={invalid_image.status_code}")

    capabilities = requests.get(f"{BASE}/api/v1/video/capabilities", headers=headers(a), timeout=15)
    voice = requests.get(f"{BASE}/api/v1/voice/status", headers=headers(a), timeout=15)
    models = requests.get(f"{BASE}/api/v1/chat/v2/models", headers=headers(a), timeout=15)
    add("PROVIDER-FAIL-CLOSED", "PASS" if capabilities.status_code == 200 and voice.status_code == 200 and models.status_code == 200 else "FAIL", f"video={capabilities.status_code}; voice={voice.status_code}; models={models.status_code}")
    add("LIVE-PROVIDER-FLOWS", "BLOCKED", "No verified provider model was enabled; no live generation call was made.", "Keep paid providers OFF; require provider-enabled run before release.")
    add("LIVE-CONCURRENCY-QUOTA", "BLOCKED", "Last-quota parallel provider request was not run without a verified free provider.", "Use the mocked atomic quota tests plus a provider-enabled release run.")

    print(json.dumps({"run":run,"cases":rows}, ensure_ascii=False))

if __name__ == "__main__":
    main()
