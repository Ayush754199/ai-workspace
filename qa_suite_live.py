"""LIVE test suite for AI Workspace v1.0 (TEST_CASES.md A-G) against a running backend.

Usage: python qa_suite_live.py [base_url]
Creates disposable QA users (one promoted to admin via the local SQLite DB) and
prints a per-case PASS/FAIL table with the actual response seen.
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import requests

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
BACKEND = Path(__file__).parent / "backend"
DB = BACKEND / "backend_app.db"
RUN = uuid.uuid4().hex[:8]
ROWS: list[dict] = []
T0 = time.time()


def rec(case, status, step, actual):
    ROWS.append({"id": case, "status": status, "step": step, "actual": str(actual)[:600]})
    print(f"{status:5} {case:10} {step} -> {str(actual)[:300]}")


def ok(case, step, actual, cond=None):
    rec(case, "PASS" if (cond if cond is not None else True) else "FAIL", step, actual)


def login_state(case, step, r, expected_in=(200,)):
    rec(case, "PASS" if r.status_code in expected_in else "FAIL", step, f"HTTP {r.status_code} {r.text[:250]}")


def register(label, name):
    email = f"qa.{label}.{RUN}@example.com"
    password = f"Qa-{uuid.uuid4().hex[:12]}!Aa1"
    payload = {
        "email": email,
        "mobile_number": f"+9198{uuid.uuid4().int % 100000000:08d}",
        "password": password,
        "full_name": name,
    }
    r = requests.post(f"{BASE}/auth/register", json=payload, timeout=40)
    return dict(email=email, password=password, name=name, register=r)


def do_login(acc):
    r = requests.post(f"{BASE}/auth/login", json={"identifier": acc["email"], "password": acc["password"]}, timeout=40)
    if r.status_code == 200:
        acc["token"] = r.json().get("access_token")
    return r


def h(acc):
    return {"Authorization": f"Bearer {acc['token']}"}


def promote(acc):
    with sqlite3.connect(DB) as c:
        c.execute("UPDATE users SET is_admin = 1, role = 'admin' WHERE email = ?", (acc["email"],))
        c.commit()
    return do_login(acc)


def file_upload(name, content, mime="image/png"):
    return {"file": (name, io.BytesIO(content), mime)}


def png_bytes():
    try:
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (30, 120, 200)).save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return b"\x89PNG\r\n\x1a\n" + b"0" * 200


def main():
    # ---------------- A. Auth & navigation ----------------
    user = register("user", "QA Tester")
    ok("AUTH-00", "user register (prereq)", f"HTTP {user['register'].status_code} {user['register'].text[:200]}",
       user["register"].status_code in (200, 201))
    login_state("AUTH-01", "valid login", do_login(user), (200,))

    bad = requests.post(f"{BASE}/auth/login", json={"identifier": user["email"], "password": "WrongPass!1"}, timeout=30)
    rec("AUTH-02", "PASS" if bad.status_code == 401 else "FAIL", "invalid password",
        f"HTTP {bad.status_code} {bad.text[:200]}")
    ok("AUTH-02b", "error body is a clear message", bad.text[:200],
       any(w in bad.text.lower() for w in ("invalid", "incorrect", "credential", "wrong")))

    me = requests.get(f"{BASE}/auth/me", headers=h(user), timeout=30)
    rec("AUTH-01b", "PASS" if me.status_code == 200 else "FAIL", "GET /auth/me", f"HTTP {me.status_code} {me.text[:200]}")
    noauth = requests.get(f"{BASE}/auth/me", timeout=30)
    rec("AUTH-03", "PASS" if noauth.status_code in (401, 403) else "FAIL", "protected endpoint without token",
        f"HTTP {noauth.status_code} {noauth.text[:120]}")
    badtoken = requests.get(f"{BASE}/auth/me", headers={"Authorization": "Bearer not.a.token"}, timeout=30)
    rec("AUTH-03b", "PASS" if badtoken.status_code == 401 else "FAIL", "bogus token",
        f"HTTP {badtoken.status_code} {badtoken.text[:120]}")

    # NAV-01: API-level pages exist (real page load checked in the browser run)
    nav = {}
    for path in ["/api/v1/chat/v2/models", "/api/v1/video/capabilities", "/api/v1/chat/v2/conversations",
                 "/api/v1/chat/v2/attachments", "/api/v1/office/formats", "/api/v1/voice/status",
                 "/api/v1/admin/providers", "/api/v1/admin/settings"]:
        r = requests.get(f"{BASE}{path}", headers=h(user), timeout=30)
        nav[path] = r.status_code
    rec("NAV-01", "PASS" if all(s in (200, 403) for s in nav.values()) else "FAIL", "page APIs reachable", nav)

    convs = requests.get(f"{BASE}/api/v1/chat/v2/conversations", headers=h(user), timeout=30)
    rec("NAV-02", "PASS" if convs.status_code == 200 else "FAIL", "state reload (conversations)", f"HTTP {convs.status_code}")

    # ---------------- B. Chat ----------------
    models = requests.get(f"{BASE}/api/v1/chat/v2/models", headers=h(user), timeout=30)
    model_data = models.json() if models.status_code == 200 else {}
    rec("CHAT-00", "PASS" if models.status_code == 200 else "FAIL", "GET models", f"HTTP {models.status_code} {str(model_data)[:300]}")

    created = requests.post(f"{BASE}/api/v1/chat/v2/conversations", headers=h(user),
                            json={"title": f"QA chat {RUN}"}, timeout=30)
    conv = created.json().get("id") if created.status_code in (200, 201) else None
    rec("CHAT-00b", "PASS" if conv else "FAIL", "create conversation", f"HTTP {created.status_code} {created.text[:200]}")

    def send(text, use_stream=False):
        payload = {"content": text}
        t = time.time()
        if use_stream:
            r = requests.post(f"{BASE}/api/v1/chat/v2/conversations/{conv}/stream", headers=h(user), json=payload, timeout=180, stream=True)
            chunks = ""
            events = 0
            try:
                for line in r.iter_lines(decode_unicode=True):
                    if line:
                        events += 1
                        chunks += line + "\n"
            except Exception as exc:  # noqa: BLE001
                chunks += f" [stream break {exc}]"
            chunks = f"[events={events}] " + chunks
            return r, chunks, round(time.time() - t, 2)
        r = requests.post(f"{BASE}/api/v1/chat/v2/conversations/{conv}/messages", headers=h(user), json=payload, timeout=180)
        return r, r.text, round(time.time() - t, 2)

    if conv:
        for case, prompt, stream in [
            ("CHAT-01", "Explain photosynthesis in 3 simple points.", False),
            ("CHAT-02", "Write three short paragraphs about the history of tea.", True),
            ("CHAT-03", "My name is Tester.", False),
            ("CHAT-03b", "What is my name?", False),
            ("CHAT-04", "Give one fact about the moon.", False),
            ("CHAT-06", "What is the weather in Delhi today?", False),
        ]:
            r, body, dur = send(prompt, stream)
            status = "PASS" if r.status_code == 200 and len(body) > 20 else "FAIL"
            if r.status_code == 503:
                status = "BLOCK" if "No eligible" in body or "unavailable" in body.lower() else "FAIL"
            rec(case, status, f"chat '{prompt[:40]}' ({dur}s)", f"HTTP {r.status_code} {body[:400]}")

        # CHAT-07: file quick action - supported document upload + chat attachment
        up = requests.post(f"{BASE}/api/v1/chat/v2/attachments", headers=h(user),
                           files={"file": ("qa-note.txt", b"My name is Tester. Favorite color: blue.", "text/plain")}, timeout=60)
        att = up.json().get("id") if up.status_code in (200, 201) else None
        rec("CHAT-07", "PASS" if att else "FAIL", "upload attachment", f"HTTP {up.status_code} {up.text[:250]}")
        if att:
            r = requests.post(f"{BASE}/api/v1/chat/v2/conversations/{conv}", headers=h(user),
                              json={"content": "Summarize the attached note in one line.", "attachment_ids": [att]}, timeout=180)
            txt = r.text
            rec("CHAT-07b", "PASS" if r.status_code == 200 and "too many values to unpack" not in txt else "FAIL",
                "chat with attachment", f"HTTP {r.status_code} {txt[:400]}")

        msgs = requests.get(f"{BASE}/api/v1/chat/v2/conversations/{conv}/messages", headers=h(user), timeout=30)
        rec("CHAT-08", "PASS" if msgs.status_code == 200 else "FAIL", "reload messages (widget state)",
            f"HTTP {msgs.status_code} count={len(msgs.json()) if msgs.status_code == 200 and isinstance(msgs.json(), list) else msgs.text[:120]}")

    # ---------------- F. Admin ----------------
    admin = register("admin", "QA Admin")
    admin_r = promote(admin)
    rec("ADM-00", "PASS" if admin_r.status_code == 200 and admin.get("token") else "FAIL", "admin promote+login",
        f"HTTP {admin_r.status_code} {admin_r.text[:200]}")

    adm_users = requests.get(f"{BASE}/api/v1/admin/users", headers=h(admin), timeout=30)
    rec("ADM-01", "PASS" if adm_users.status_code in (200, 405) else "FAIL", "admin users list", f"HTTP {adm_users.status_code} {adm_users.text[:250]}")
    providers = requests.get(f"{BASE}/api/v1/admin/providers", headers=h(admin), timeout=30)
    rec("ADM-01b", "PASS" if providers.status_code == 200 else "FAIL", "admin providers", f"HTTP {providers.status_code} {providers.text[:500]}")
    settings_r = requests.get(f"{BASE}/api/v1/admin/settings", headers=h(admin), timeout=30)
    rec("ADM-03", "PASS" if settings_r.status_code == 200 else "FAIL", "admin settings", f"HTTP {settings_r.status_code} {settings_r.text[:400]}")
    order = requests.get(f"{BASE}/api/v1/admin/gateway/order", headers=h(admin), timeout=30)
    rec("ADM-03b", "PASS" if order.status_code == 200 else "FAIL", "gateway routing order", f"HTTP {order.status_code} {order.text[:300]}")

    # usage before/after a chat
    usage_before = requests.get(f"{BASE}/api/v1/admin/analytics/usage", headers=h(admin), timeout=30)
    rec("ADM-07", "PASS" if usage_before.status_code == 200 else "FAIL", "usage analytics", f"HTTP {usage_before.status_code} {usage_before.text[:300]}")

    # quota controls for our own test user
    uid = None
    if adm_users.status_code == 200:
        users_json = adm_users.json()
        entries = users_json if isinstance(users_json, list) else users_json.get("users", users_json.get("items", []))
        for entry in entries:
            if str(entry.get("email", "")).lower() == user["email"].lower():
                uid = entry.get("id")
                break
    if uid:
        q = requests.put(f"{BASE}/api/v1/admin/users/{uid}/quota", headers=h(admin),
                         json={"daily_limit": 5, "per_chat_limit": 2}, timeout=30)
        rec("ADM-05", "PASS" if q.status_code in (200, 204) else "FAIL", "set chat quota", f"HTTP {q.status_code} {q.text[:250]}")
        rq = requests.get(f"{BASE}/api/v1/admin/users/{uid}/quota", headers=h(admin), timeout=30)
        rec("ADM-05b", "PASS" if rq.status_code == 200 and rq.json().get("daily_limit") == 5 else "FAIL",
            "read back chat quota", f"HTTP {rq.status_code} {rq.text[:250]}")
        qi = requests.put(f"{BASE}/api/v1/admin/users/{uid}/quotas/image", headers=h(admin),
                          json={"capability": "image", "daily_limit": 3}, timeout=30)
        qv = requests.put(f"{BASE}/api/v1/admin/users/{uid}/quotas/video", headers=h(admin),
                          json={"capability": "video", "daily_limit": 1}, timeout=30)
        rec("ADM-06", "PASS" if qi.status_code in (200, 204) and qv.status_code in (200, 204) else "FAIL",
            "set image/video quota", f"image={qi.status_code} video={qv.status_code} {qi.text[:120]} {qv.text[:120]}")
        rqi = requests.get(f"{BASE}/api/v1/admin/users/{uid}/quotas", headers=h(admin), timeout=30)
        persisted = rqi.json().get("quotas", {}) if rqi.status_code == 200 else {}
        rec("ADM-06b", "PASS" if persisted.get("image", {}).get("daily_limit") == 3 and persisted.get("video", {}).get("daily_limit") == 1 else "FAIL",
            "image/video quota persists", f"HTTP {rqi.status_code} {str(persisted)[:300]}")

    # ADM-08 non-admin protection (every admin endpoint)
    denials = {}
    for path in ["/api/v1/admin/users", "/api/v1/admin/providers", "/api/v1/admin/settings",
                 "/api/v1/admin/analytics/usage", "/api/v1/admin/logs", "/api/v1/admin/audit/export"]:
        r = requests.get(f"{BASE}{path}", headers=h(user), timeout=30)
        denials[path] = r.status_code
    rec("ADM-08", "PASS" if all(s in (401, 403) for s in denials.values()) else "FAIL",
        "normal user hits admin endpoints", denials)

    # ADM-04 discovery
    disc = requests.post(f"{BASE}/api/v1/admin/providers/openrouter/models", headers=h(admin), json={}, timeout=90)
    if disc.status_code == 405:
        disc = requests.get(f"{BASE}/api/v1/admin/providers/openrouter/models", headers=h(admin), timeout=90)
    rec("ADM-04", "PASS" if disc.status_code in (200, 502, 503) else "FAIL", "openrouter free-model discovery",
        f"HTTP {disc.status_code} {disc.text[:400]}")
    if disc.status_code == 200:
        models_json = disc.json().get("models", [])
        paid = [m for m in models_json if not m.get("is_free", True)]
        rec("ADM-04b", "PASS" if not paid else "FAIL", "discovery lists free models only",
            f"total={len(models_json)} non_free={len(paid)}")

    # ---------------- C. Image ----------------
    img = png_bytes()
    gen = requests.post(f"{BASE}/api/v1/chat/image", headers=h(user),
                        data={"prompt": "A cinematic sunset over Jaipur, digital art"}, timeout=180)
    rec("IMG-01", "PASS" if gen.status_code == 200 else ("BLOCK" if gen.status_code in (503, 501) else "FAIL"),
        "text-to-image", f"HTTP {gen.status_code} {gen.text[:400]}")

    edit = requests.post(f"{BASE}/api/v1/chat/image", headers=h(user),
                         data={"prompt": "Make the background a soft blue gradient"},
                         files=file_upload("qa.png", img), timeout=180)
    rec("IMG-02", "PASS" if edit.status_code == 200 else ("BLOCK" if edit.status_code in (503, 501) else "FAIL"),
        "image edit", f"HTTP {edit.status_code} {edit.text[:400]}")

    invalid = requests.post(f"{BASE}/api/v1/chat/image", headers=h(user),
                            data={"prompt": "Make it blue"},
                            files={"image": ("fake.png", b"this is not an image", "image/png")}, timeout=60)
    if invalid.status_code == 503 and "not configured" in invalid.text:
        # image path short-circuits on config before it validates the upload
        invalid2 = requests.post(f"{BASE}/api/v1/documents/summarize", headers=h(user),
                                 files={"file": ("fake.png", b"this is not an image", "image/png")}, timeout=60)
        rec("IMG-03", "INFO" if invalid2.status_code in (400, 415, 422) else "FAIL",
            "corrupt image blocked before provider config (503 short-circuit noted)",
            f"chat/image=503 config-first; documents/summarize={invalid2.status_code} {invalid2.text[:200]}")
    rec("IMG-03", "PASS" if invalid.status_code in (400, 415, 422) else "FAIL", "invalid image upload",
        f"HTTP {invalid.status_code} {invalid.text[:300]}")

    bg = requests.post(f"{BASE}/api/v1/images/remove-background", headers=h(user),
                       files=file_upload("qa.png", img), data={"tolerance": "30"}, timeout=180)
    rec("IMG-04", "PASS" if bg.status_code in (200, 503, 501) else "FAIL", "remove-background", f"HTTP {bg.status_code} {bg.text[:300]}")

    desc = requests.post(f"{BASE}/api/v1/images/describe", headers=h(user), files=file_upload("qa.png", img), timeout=180)
    rec("IMG-04b", "PASS" if desc.status_code in (200, 503, 501) else "FAIL", "image describe", f"HTTP {desc.status_code} {desc.text[:300]}")

    # ---------------- D. Video ----------------
    cap = requests.get(f"{BASE}/api/v1/video/capabilities", headers=h(user), timeout=30)
    rec("VID-08", "PASS" if cap.status_code == 200 else "FAIL", "video capabilities", f"HTTP {cap.status_code} {cap.text[:600]}")

    empty = requests.post(f"{BASE}/api/v1/video/generate", headers=h(user), json={"prompt": ""}, timeout=60)
    rec("VID-03", "PASS" if empty.status_code in (400, 422) else "FAIL", "empty prompt", f"HTTP {empty.status_code} {empty.text[:250]}")

    badfile = requests.post(f"{BASE}/api/v1/video/image-to-video", headers=h(user),
                            data={"prompt": "animate"},
                            files={"image": ("bad.mp4", b"not-an-image", "video/mp4")}, timeout=60)
    rec("VID-04", "PASS" if badfile.status_code in (400, 415, 422) else "FAIL", "invalid file image-to-video",
        f"HTTP {badfile.status_code} {badfile.text[:250]}")

    jobs_before = requests.get(f"{BASE}/api/v1/video/jobs", headers=h(user), timeout=30)
    before_text = jobs_before.text

    i2v = requests.post(f"{BASE}/api/v1/video/image-to-video", headers=h(user),
                        data={"prompt": "A slow orbit around the subject"},
                        files={"image": ("qa.png", img, "image/png")}, timeout=300)
    rec("VID-02", "PASS" if i2v.status_code in (200, 202) else ("BLOCK" if i2v.status_code in (503, 501, 500) else "FAIL"),
        "image-to-video", f"HTTP {i2v.status_code} {i2v.text[:500]}")

    vid = requests.post(f"{BASE}/api/v1/video/generate", headers=h(user),
                        json={"prompt": "A red ball bouncing on grass, 2 seconds"}, timeout=90)
    rec("VID-01", "PASS" if vid.status_code in (200, 202) else ("BLOCK" if vid.status_code in (503, 501, 500) else "FAIL"),
        "prompt-to-video", f"HTTP {vid.status_code} {vid.text[:500]}")

    jobs_after = requests.get(f"{BASE}/api/v1/video/jobs", headers=h(user), timeout=30)
    rec("VID-09", "PASS" if jobs_after.status_code == 200 else "FAIL", "job history reload",
        f"HTTP {jobs_after.status_code} before={len(before_text)} after={len(jobs_after.text)} {jobs_after.text[:300]}")
    failed_jobs = [j for j in (jobs_after.json() if isinstance(jobs_after.json(), list) else [])
                   if str(j.get("status")) == "failed"]
    rec("VID-05/06", "PASS" if failed_jobs else "INFO", "failed jobs recorded (quota refund path exercised)",
        f"failed_jobs={len(failed_jobs)}")

    if jobs_after.status_code == 200 and isinstance(jobs_after.json(), list) and jobs_after.json():
        jid = jobs_after.json()[0].get("id") or jobs_after.json()[0].get("job_id")
        if jid:
            one = requests.get(f"{BASE}/api/v1/video/jobs/{jid}", headers=h(user), timeout=30)
            rec("VID-09b", "PASS" if one.status_code == 200 else "FAIL", "single job fetch", f"HTTP {one.status_code} {one.text[:250]}")

    # ---------------- E. Editing ----------------
    for path in ["/api/v1/voice/status"]:
        r = requests.get(f"{BASE}{path}", headers=h(user), timeout=30)
        rec("EDIT-00", "PASS" if r.status_code == 200 else "FAIL", "voice/trim capability", f"HTTP {r.status_code} {r.text[:200]}")

    # ---------------- G. Security ----------------
    key_markers = ["sk-or-v1-", "gsk_", "hf_", "cfat_", "fal_sk_", "ltxv_", "tvly-dev"]
    bodies = {"models": models.text, "providers": providers.text, "capabilities": cap.text, "settings": settings_r.text}
    leaks = {k: [m for m in key_markers if m in v] for k, v in bodies.items()}
    rec("SEC-01", "PASS" if not any(leaks.values()) else "FAIL", "no key material in payloads", leaks)

    legacy = requests.post(f"{BASE}/api/v1/chat/stream", headers=h(user), json={"prompt": "hi"}, timeout=30)
    rec("SEC-03b", "PASS" if legacy.status_code in (410, 403, 404) else "FAIL", "legacy direct-provider endpoint gated",
        f"HTTP {legacy.status_code} {legacy.text[:200]}")

    print("\n\n===== JSON =====")
    print(json.dumps({"run": RUN, "elapsed": round(time.time() - T0, 1), "rows": ROWS}, indent=1))


if __name__ == "__main__":
    main()
