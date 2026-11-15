# Live Scenario Audit — 2026-11-04 (revision 2)

Provider quota consumed: **0%**. Every provider-shaped scenario ran against a **local fake OpenRouter** (`backend/tests/test_provider_contract_mock.py`, binds `127.0.0.1`) or a mocked gateway. Live application checks ran against an isolated API (`backend/.env.qa`: no Mongo, no provider keys) on `127.0.0.1:8004`.

Workbook: **`AI_Workspace_Scenario_Audit_2026-11-04_NEW.xlsx`** (Summary / Results / Findings / Warnings / Status Counts).

## 1. Why registration hung, and the fix
- **Root cause.** `POST /auth/register` → `ensure_mongo_indexes()` → `mongo.users.create_index()`. Motor's default `serverSelectionTimeoutMS` is 30 s; when the Atlas cluster stopped being selectable the `await` never returned within the client timeout, so the request hung (and `run_release_live_audit.py` hit the identical timeout). It "worked on 2026-11-03" because the cluster was reachable that day — nothing in the code changed.
- **Fix.** `MongoCollections.verify_ready()` pings with `asyncio.wait_for(..., 2s)`; register/login/`mongo_user`/index creation call it and return **503 `Database temporarily unavailable. Please retry shortly.` + `Retry-After: 2`**. Login no longer swallows `HTTPException`.
- **Proof (live, real process).** `qa_db_fail_live.py` with `MONGODB_URL=mongodb://10.255.255.1:27017?serverSelectionTimeoutMS=30000` → register **503 in 2.09 s**, login **503 in 2.03 s**. Unit: `backend/tests/test_db_fail_fast.py`.
- **Isolated QA DB:** no `mongod` exists on this machine (checked), so the "QA Mongo" role is played by the isolated SQLite QA database (`backend/.env.qa`, `MONGODB_URL=`). No production/Atlas data was touched.

## 2. Prior "live audit" claims re-cited
`AI_Workspace_Extended_Live_Audit_Log.txt` (audit date **2026-11-03**, localhost 127.0.0.1:8000) is the only prior live evidence. Every row that leaned on it is now either re-proven today (`qa_live_rooms.py`, 2026-11-04) or downgraded to **Pass (Automated)** with the test file named. Notably, the 2026-11-03 `ROOM-01/05` PASSes were produced by a build where the SQLite JSON membership bug below also existed — they are invalidated, not carried forward.

## 3. Provider contract (fake OpenRouter)
Stream disconnect, timeout, 429, 5xx, malformed JSON, fallback order, parallel last quota, double submit, all-routes-fail refund: all in `backend/tests/test_provider_contract_mock.py` — **Pass (Mocked)**. Discovered defects: malformed JSON escaped as `requests.JSONDecodeError`; the OpenRouter timeout was hard-coded to 120 s ignoring settings. Both fixed.

## 4. Video jobs
`_jobs` in-process dict replaced by `MediaJob` rows (`request_id`, `reservation_id`, status/payload). `recover_interrupted_jobs()` runs on startup: queued/processing → `failed` + quota refund. Tests: `backend/tests/test_video_jobs_persistence.py`.

## 5. Quota reset — decision needed
`quota.CLOCK_TIMEZONE` is the single knob (currently `timezone.utc`). `backend/tests/test_quota_day_rollover.py` proves both policies (UTC midnight and IST 18:30 UTC). **Question: should production reset at UTC midnight or IST midnight?** Nothing changes until you answer.

## 6. ADM-10
`backend/tests/test_adm10_no_provider.py`: model list is `200` with `auto.selectable=false`, reason `"No eligible free provider is configured or enabled"`, no selectable route; chat send and stream return **503** with the same message; **zero** messages and **zero** quota rows written.

## 7. Chat rooms — genuine live run (`qa_live_rooms.py`, 2026-11-04)
ROOM-03/04/05/08/09/10, ACL-01..07, MSG-01/02/06/07/08/10, ADM-CR-02..12 pass live. Two real defects surfaced and were fixed:
- **Membership:** a one-member room's `members` column came back as `71` instead of `[71]`, so the owner got 403 on their own room (`backend/app/models/room.py::MemberList`).
- **Admin combined filter:** `username` and `role` matched different members, so `role=doctor&username=sharma` returned rooms with no matching person (`admin.py`).
- Sender tag verified live: `Dr. Ramesh Sharma (doctor)`.
- Surviving limitation: **ADM-CR-13** (revoked admin) is only as fresh as the next `/auth/me`/login projection → **Partial**. ADM-CR-11 (>1000 msgs live) → **Partial** (120 msgs automated).
- **PWA-01/03** were measured at 360 px in headless Edge against a static replica of the audit layout (`qa_mobile_measure.html` → `qa_360_dump.html`: `iframeWidth 360`, `horizontalOverflow false`, filters scrollable, transcript wraps, single-column grid). Real authenticated mobile, iOS/Safari, install/offline (**PWA-02/04–08**) → **Blocked**.

## 8. Secrets — findings (no values printed)
- Tracked `.env` files: **none** (only `.env.example` variants) — pass.
- **Fail (confirmed, value-identity, not just shape):** `qa_secret_value_scan.py` loaded 43 local values and matched them byte-for-byte inside 779 committed git objects. Hits in `backend/tests/test_phases_chat_docs.py` for **`GROQ_API_KEY_1`, `OPENROUTER_API_KEY_1`, `HF_TOKEN_1`, `LTX_TOKEN_1`** — i.e. the real keys from `backend/.env` are in history. HEAD now has synthetic `"aaa…"` fixtures, but history still holds the originals. **Rotate all four keys now** (and treat any `backend/.env` key as compromised). Full output: `qa_logs/secret_history_scan.txt`.
- `*.env.example -> GEMINI_KEY_2..16 / GEMINI_KEY_1` hits are the `replace-with-…` placeholder lines matching an env value that is itself a placeholder — no secret, listed for transparency.

## 9. The deprecation warnings
1. `backend/app/core/database.py:21` — SQLAlchemy `MovedIn20Warning`: `declarative_base()`.
2. `backend/app/schemas/user.py:18` — Pydantic v2: class-based `Config`.
3. `backend/app/main.py:163` — FastAPI: `@app.on_event("startup")` deprecated, use lifespan.
4. `.venv/.../fastapi/applications.py:4723` — FastAPI internal `router.on_event` call (same API).
5. `.venv/.../passlib/handlers/argon2.py:716` — accessing `argon2.__version__`.
6. *(new)* `PIL/Image.py:3452` — `DecompressionBombWarning` from the new 10000×10000 EDGE-04 fixture; consider rejecting >89 MP uploads explicitly.

Frontend: `npx vitest run` → **23 passed / 6 files** (the runner exits; the earlier "watch mode" note is gone).

## Final verdict
Automated suite (213 passed), frontend suite, build, and the authenticated live room run are green. Remaining red flags: the **committed key history (rotate)**, **ADM-CR-13/PWA device coverage**, and the **open UTC-vs-IST quota-day decision**.
