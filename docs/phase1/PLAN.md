# Phase 1 Implementation Plan — AI Workspace v1.0

Status: in progress. Written BEFORE any source change, after a read-only inspection
of the files listed under each item. No secret values appear in this document.

Baseline measured on this machine before any edit (`backend/`, `python -m pytest`):

| Metric | Result |
|---|---|
| Collected | 308 tests |
| Passed | 238 |
| Failed | 6 |
| Errors | 64 |
| Duration | 286 s |

Baseline failures/errors that Phase 1 must resolve:

1. `test_db_fail_fast::test_login_reports_503_when_database_is_down` — `pytest.raises`/TestClient
   startup fails with `RuntimeError: Event loop is closed`.
2. 63 further `ERROR` results in `test_phases_chat_docs.py` and `test_spa_routing.py` — each is a
   TestClient lifespan startup failure caused by the same root cause.
3. `test_db_fail_fast::test_mongo_ping_fails_fast_with_503` — register took 17 s instead of < 8 s
   because Mongo is contacted before the password/identity check.
4. `test_db_fail_fast::test_application_shutdown_closes_mongo_client` — the shutdown hook never runs
   (router-level `on_event`).
5. `test_phases_chat_docs::test_me_falls_back_to_sql_when_mongo_is_unreachable` — `/auth/me` no
   longer falls back to SQL.
6. `test_room_acl_live_qa::test_room_04_invite_by_username_and_email` — invited member does not see
   the room (order-dependent shared state).
7. `test_invalid_image_regression::test_legacy_image_endpoint_rejects_invalid_image_without_500_or_quota_use`
   — order-dependent settings mutation.

Root cause of 1–3: `backend/app/api/v1/endpoints/calls.py` registers
`@router.on_event("startup")`, which calls `ensure_call_indexes()`. That coroutine touches the Motor
client captured at import time. Starlette creates a new event loop per `TestClient`, so the Motor
client's executor is bound to a closed loop. Separately, when the ping is slow (`_HangingClient`),
`serverSelectionTimeoutMS=5000` plus Motor's socket wait consumed 17 s before the 4 s `wait_for`
could even start counting.

---

## 1. Security

### 1.1 Secrets in the working tree and Git index

* Existing implementation: `.env` (root), `backend/.env`, `backend/.env.qa`, `backend/.env.qa-mongo`
  exist locally. `git ls-files --error-unmatch` reports **none of them are tracked**. `.gitignore`
  already ignores `.env`, `.env.*` (with `!.env.example`) and `**/.env`.
* Exact bug: no tracked secret file, but `qa_logs/secret_history_scan.txt` and
  `backend/tests/test_phases_chat_docs.py` show that values matching the local `.env` names appear in
  committed history (in `.env.example` at several revisions, and in a test file). The `.env.example`
  files also contain a **real-looking cluster host** (`cluster0.mqn98sf...`) which is not a
  placeholder.
* Fix: verify the index again, `git rm --cached` only if a secret file is actually tracked (no-op
  here, nothing deleted on disk), replace every non-placeholder in the three `.env.example` files
  with placeholders, add `ADMIN_BOOTSTRAP_IDENTIFIER` / `TRUSTED_PROXY_*` documentation, and write a
  rotation list.
* Files: `.gitignore`, `.env.example`, `backend/.env.example`, `frontend/.env.example`,
  `docs/phase1/SECRET_ROTATION.md` (new).
* Regression risk: low. `.env.example` is documentation; Docker/Render read `.env`.
* Not doing: rewriting history or force-pushing. Historical values are treated as **compromised**.

### 1.2 Hard-coded admin identifier `7541996529`

* Existing implementation: `backend/app/api/v1/endpoints/auth.py:239`
  `bootstrap_identifier = settings.ADMIN_BOOTSTRAP_IDENTIFIER.strip() or "7541996529"`. The login path
  then silently promotes that account to admin (and creates it as admin on first login).
  `backend/app/main.py:250` already contains an env-controlled bootstrap `UPDATE`, which is the safe
  mechanism.
* Exact bug: anyone able to register or log in with that identifier becomes administrator in
  production with no environment configuration at all.
* Fix: delete the literal; only `settings.ADMIN_BOOTSTRAP_IDENTIFIER` (explicitly set) may promote.
  Registration never sets `is_admin`. The startup bootstrap keeps its env-controlled behaviour and is
  narrowed to `role='user'` rows only (never demotes, never touches existing admins).
* Files: `backend/app/api/v1/endpoints/auth.py`, `backend/app/main.py`, `backend/tests/test_admin_provisioning.py` (new).
* Regression risk: the legitimate admin must now be provisioned via
  `ADMIN_BOOTSTRAP_IDENTIFIER`; documented in the deployment checklist.

### 1.3 JWT configuration

* Existing: `settings.JWT_SECRET` has `min_length=32`, a small deny-list, and `_load_settings()`
  replaces a missing/short secret with a random per-process value (users are signed out on restart,
  and on a multi-instance deployment a token minted by instance A is invalid on instance B).
  `ACCESS_TOKEN_EXPIRE_MINUTES` defaults to 10080 (7 days). `deps.get_current_user` re-reads the row
  on every request, so revocation and deactivation are already immediate.
* Exact bug: a production deployment can boot with an ephemeral signing key (silently "working"
  until the next restart) and with an unknown secret strength.
* Fix: expose `ENVIRONMENT` (`development` | `production`). In production a missing/short
  `JWT_SECRET` is a **startup error**; outside production the existing fallback stays. Keep
  `iat`/`exp` claims and add `token_type` to new tokens without removing the accepted shape of old
  tokens (no refresh tokens — explicitly out of scope). Document the migration path.
* Files: `backend/app/core/config.py`, `backend/app/core/security.py`, `backend/tests/test_security_config.py`.
* Regression risk: a local run with no `JWT_SECRET` still works (development); a *production* start
  without it now fails fast — that is the point, and it is documented.

### 1.4 Rate limiting / `X-Forwarded-For`

* Existing: `app/core/rate_limit.py:client_ip()` trusts the first `X-Forwarded-For` entry from any
  caller, so a client can rotate the header to defeat both limiters. Uvicorn is started with
  `--proxy-headers --forwarded-allow-ips="*"`.
* Fix: only honour forwarding headers when the *socket* peer is inside `TRUSTED_PROXY_CIDRS`
  (default: loopback + RFC1918 so local nginx/Docker works, never a public client). Walk the
  `X-Forwarded-For` chain right-to-left and stop at the first untrusted hop. `forwarded-allow-ips`
  in Render/Docker becomes the trusted set instead of `*`.
* Files: `backend/app/core/rate_limit.py`, `backend/app/core/config.py`, `render.yaml`,
  `backend/Dockerfile`, `backend/tests/test_rate_limit.py`.
* Regression risk: behind an unlisted proxy every request shares one bucket; the default covers
  Docker/nginx on a private network and Render (which connects from a private address space) and the
  setting is documented.

### 1.5 Error and log leakage

* Existing: several endpoints return `str(exc)` / `f"...{exc}"` to the client
  (`documents.py` extract/ask/summarize/generate, `chat_v2.py` generate-document, `voice.py`
  transcription, `research.py`), and `research.py` has a syntactically broken second `except`
  (`app.documents.py:47` in the baseline log) that would mask failures. The logger redacts known key
  formats but error text from third-party libraries can still carry URLs with query strings.
* Fix: a single `app.core.errors.public_message()` helper plus a global exception handler that logs
  with `log_connection_failure` (redaction already implemented in `backend/logger.py`) and returns a
  generic message with the `X-Request-Id`. Replace the user-facing `str(exc)` in the affected
  handlers with curated messages.
* Files: `backend/app/core/errors.py` (new), `backend/app/main.py`, `documents.py`, `chat_v2.py`,
  `voice.py`, `research.py`, `ai.py`, tests.
* Regression risk: clients that matched on internal error strings would see new text; tests that
  assert on messages are reviewed.

---

## 2. Deployment

### 2.1 Dependency mismatch (Render never installs FastAPI)

* Existing: `render.yaml` builds with `pip install -r ../requirements.txt` from `rootDir: backend`.
  `requirements.txt` (root) is the **Streamlit** file: no `fastapi`, `uvicorn`, `sqlalchemy`,
  `motor`, `python-jose`, `psycopg`. `backend/requirements.txt` is the FastAPI file. So a Render
  deploy of the API cannot import `fastapi`.
* Also: root requirements pin `python-docx`, `openpyxl`, `python-pptx`, `reportlab`, `argon2-cffi`;
  `backend/requirements.txt` pins the same packages again (two files, drifting versions), and both
  files have unpinned, floating versions.
* Fix: keep `backend/requirements.txt` as the single source of truth for the API, make the render
  build use it (`pip install -r requirements.txt` with `rootDir: backend`), and change the root
  `requirements.txt` into a documented `-r backend/requirements.txt` + Streamlit-only extras so both
  entry points install a compatible superset. Add upper bounds only where a major bump breaks the
  API (`passlib`, `python-jose`, `sqlalchemy`), no blanket upgrades.
* Files: `requirements.txt`, `backend/requirements.txt`, `render.yaml`, `backend/Dockerfile`.
* Regression risk: Docker already installs `backend/requirements.txt`, so its image is unchanged
  (Streamlit pins are additive there). No application code depends on the root file.

### 2.2 Render build/start, storage and env checklist

* Existing: `startCommand` runs `uvicorn app.main:app` with cwd `backend` and
  `--forwarded-allow-ips="*"`; `ATTACHMENT_STORAGE_DIR=/var/data/attachments` is set while
  `disk:` is commented out, so uploads are written to the ephemeral container filesystem and then
  lost on every deploy/restart. `docs/DEPLOYMENT.md` and `render.yaml` both say free plans cannot
  mount a disk, so the current value is misleading.
* Fix: keep the disk block but make it explicit and paired with the storage path; when no disk is
  configured, leave `ATTACHMENT_STORAGE_DIR` unset so the documented default is used and the
  limitation is stated in the checklist instead of implied persistence. Restrict
  `--forwarded-allow-ips`. Add `docs/phase1/DEPLOYMENT_CHECKLIST.md`.
* Files: `render.yaml`, `docs/phase1/DEPLOYMENT_CHECKLIST.md` (new), `docs/DEPLOYMENT.md`.
* Regression risk: existing deployments keep working; the storage path change only matters where a
  disk is actually mounted (documented).

### 2.3 Database startup / lifespan

* Existing: `ensure_schema()` runs at import; a FastAPI `lifespan` only closes Mongo; a router-level
  `@router.on_event("startup")` in `calls.py` calls `ensure_call_indexes()`; `@app.on_event("startup")`
  re-runs `ensure_schema()` and `recover_interrupted_jobs()`; `CALL_DATA_RESET_ON_STARTUP` is handled
  in **two** places (`calls.py` and `main.py`).
* Exact bug: the router-level hook is what raises `RuntimeError: Event loop is closed` for every
  `TestClient` after the first; the duplicate hooks run work twice; a Mongo outage in the hook takes
  down startup.
* Fix: move all startup work into `app/lifespan.py` (`init_database`, `recover_interrupted_jobs`,
  optional call-data reset, `ensure_call_indexes` guarded by `mongo.enabled` **and** wrapped so a
  Mongo failure is logged, never fatal). Delete `calls.py`'s hook and `main.py`'s `on_event`.
  `ensure_schema()` stays at import (documented reason: endpoints may run without lifespan under
  scripts/TestClient) but the bootstrap promotion is idempotent and env-gated.
* Files: `backend/app/lifespan.py` (new), `backend/app/main.py`, `backend/app/api/v1/endpoints/calls.py`,
  `backend/app/api/v1/endpoints/video.py`, `backend/app/services/call_service.py`, tests.
* Regression risk: any deploy script that relied on the router hook ordering now gets one ordering;
  SQLite/Postgres schema creation is unchanged (no drops, no destructive statements).

---

## 3. AI gateway: fresh-database routing

* Existing: `AIGateway.eligible_routes(db=...)` does
  `if rows: registry = <database rows>`. With an empty `provider_models` table `rows` is empty, so
  the static `MODEL_REGISTRY` is used — but `if db is not None` and rows exist the *static* registry
  is discarded, and `GeminiGatewayProvider` is constructed for a route whose `verified_at` came from
  the DB. The static registry only marks `openrouter` as verified.
* Exact bug (fresh DB): `resolve_model()` / `gateway.generate_with_reservation()` raise
  "No eligible free AI provider is available" when `provider_models` is empty even though
  `OPENROUTER_KEY_1` is configured **and** `AI_GATEWAY_MODE` in `backend/.env` is `dev`, because
  `dev` mode returns the mock route only in `generate()`/`eligible_routes()` without a db.
  With a *partially* populated table the DB row set silently hides a statically verified provider.
* Fix: `merge_registry(static, db)` — union by `(provider, model)` with the DB row taking precedence,
  so static entries remain when the table is empty or only partly populated. Provider readiness is
  decided by **environment credentials** (`get_provider_credentials`) for every route; routes whose
  adapter/model cannot be built are skipped before any provider call. OpenRouter is the initial
  provider, Groq and Gemini adapters are preserved with their configured model IDs, and no fallback
  bills twice (each fallback reuses the same reservation).
* Files: `backend/app/services/ai_gateway/gateway.py`, `registry.py`, `chat_service.py`,
  `backend/tests/test_provider_routing.py` (new).
* Regression risk: a DB row that *disables* a model must still win, so merge precedence is
  `db > static` and a disabled DB row suppresses the static entry for the same key.

### 3.1 Central model configuration

* Existing: model IDs live in `app/core/config.py` (`GEMINI_MODEL`, `OPENROUTER_MODEL`, `GROQ_MODEL`)
  **and** in `config.py` at the repo root (`GEMINI_MODEL`, fallbacks) and **again** in
  `registry.py` (`"gemini-3.8-flash"`). `key_rotator` imports the root copy.
* Fix: `app/core/models.py` (new) resolves model IDs once from settings with the root `config.py`
  fallback list; `registry.py` builds its static entries from it. No model is invented: only IDs that
  are already present in the repository are used, and a model without a configured ID stays disabled.
* Files: `backend/app/core/models.py` (new), `registry.py`, `key_rotator.py` (import only).
* Regression risk: the root `config.py` stays importable for the Streamlit app.

---

## 4. Chat streaming and quota

* Existing (`chat_v2.stream_message`): quota is reserved **inside the generator** with
  `SessionLocal()`; the successful path commits in another session after the stream ends. The
  `finally` block only releases the concurrency limiter. If the ASGI task is cancelled
  (client disconnect, `StreamingResponse` closed) neither `commit` nor `release` runs and the
  `RESERVED` counter stays forever (the daily quota is then permanently reduced until the *next*
  day's first reservation sweeps old rows, and never within the same day).
* Related defect: failure paths call `quotas.release(...)` and then `db.commit()` on the request
  session, which can commit an unrelated pending transaction (the user message) or raise because the
  reservation was created in a different session.
* Fix: reserve before the first byte is returned (in the request), keep the reservation id, and wrap
  the whole generator in `try/except/finally` that: commits exactly once on success,
  releases on failure, and on `GeneratorExit` / `CancelledError` / any exception reconciles
  (release if nothing was consumed, otherwise mark uncertain — never double-bill). Add an
  `expired_reservation_sweep()` that runs on every reservation so an abandoned reservation can never
  exhaust the day, and use the same rules for chat and research.
* Files: `backend/app/services/ai_gateway/quota.py`, `backend/app/api/v1/endpoints/chat_v2.py`,
  `backend/app/services/chat_service.py`, `backend/tests/test_stream_quota.py` (new).
* Regression risk: quota enforcement is *not* weakened; success still commits exactly one unit.

---

## 5. OCR

* Existing: `app/services/documents.py:ocr_image()` imports `app.services.vision`, which **does not
  exist** (`ModuleNotFoundError`). Callers `chat_v2._extract_upload_text` swallow it, and
  `extract_pdf_ocr` calls it for image-only pages, so a scanned PDF silently yields "no text"
  instead of a clear error, while digital PDFs work.
* Fix: implement `app/services/vision.py` as a thin adapter over the **existing** Gemini image
  pipeline (`google-genai` + `key_rotator`), raising `OcrUnavailable` when no vision provider is
  configured; make every caller handle `OcrUnavailable` explicitly and report
  `ocr_unavailable: true` (chat upload preview and `/documents/extract` already have that flag).
  Digital extraction is untouched. No fake OCR, no invented text.
* Files: `backend/app/services/vision.py` (new), `backend/app/services/documents.py`,
  `backend/app/api/v1/endpoints/chat_v2.py`, `backend/tests/test_ocr_safety.py` (new).
* Regression risk: if `pytesseract`/`tesseract` is present locally the local engine runs first (as
  today); when neither is available the user gets a controlled message instead of empty text.

---

## 6. Test infrastructure

* Existing root cause of the 63 errors: calls router `on_event` (see 2.3). Secondary causes:
  `test_ai.py` and `test_auth.py` override `get_db` while sharing the application database file;
  `test_invalid_image_regression.py` mutates the shared `settings` object; `test_room_acl_live_qa.py`
  leaves users/rooms behind.
* Fix: the lifespan change removes the event-loop errors. Add a session-scoped `mongo_off` fixture
  that makes `mongo.enabled` False (already partly in `conftest.isolate_mongo`) and an
  autouse guard that fails the run if an outbound HTTP call is attempted during unit tests
  (`requests`/`urllib` monkeypatched). Isolate the shared SQLite file per module where it matters.
* Files: `backend/tests/conftest.py`, `backend/tests/test_auth.py`, `backend/tests/test_ai.py`,
  `backend/tests/test_room_acl_live_qa.py`, `backend/tests/test_invalid_image_regression.py`.
* Regression risk: genuine live/integration tests must remain runnable — the guard is opt-out via a
  marker, not a hard global.

---

## 7. Validation commands

```powershell
cd backend; ..\.venv\Scripts\python.exe -m pytest -q
cd frontend; npm run test -- --run
cd frontend; npm run build
docker compose config
```

Plus a live smoke test of `/api/v1/health`, `/auth/register`, `/auth/login`, `/auth/me`,
`/api/v1/chat/v2/models`, a stream, an attachment upload/download.
