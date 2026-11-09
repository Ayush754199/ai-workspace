# AI Workspace v1.0 — Phase 1 Audit & Implementation Plan

Date: 2026-11-02
Scope: full codebase inspection before any large-scale modification. No code was deleted,
no `.env` file was modified, no credentials are reproduced in this document.

---

## 1. What actually exists today (verified by reading code + running the app)

### 1.1 Runtime surfaces

| Surface | Entry point | Status |
|---|---|---|
| Streamlit app (the UI the user actually runs) | `streamlit_app.py` (~4,700 lines) | **Working, large, single-file** |
| FastAPI backend | `backend/app/main.py` → `/api/v1/*` | Imports, boots, SQLite auto-migrates |
| React frontend | `frontend/src/App.tsx` | **Skeleton only** (4 routes, no API calls wired to chat) |
| Flutter "android" app | `mobile_app/lib/**` | Partial duplicate of the Streamlit UI |
| Capacitor | — | **Not present** |
| Video engine (new) | `backend/pro_video_editor.py` | Working, tested (see §5) |

Confirmed by execution:
- `import streamlit_app` → clean.
- `backend.pro_video_editor` full render suite → 9 scenarios pass.
- `tests/test_pro_editor_e2e.py` → renders colour + transform + mask + overlay + audio + 9:16 export.
- MongoDB: **reachable from this machine right now** (see §4).

### 1.2 Feature matrix (what is real vs. not)

| # | Requirement | Current state | Verdict |
|---|---|---|---|
| 1 | AI Chat | Streamlit chat with streaming (Gemini), history in Mongo, file attach, voice, retry, copy/speak | **Working** (Gemini-only, no OpenRouter, no per-chat prompt-mode quotas) |
| 2 | PDF AI | `parse_file` + `_document_context` + Gemini context; `pypdf` text extraction | **Basic** — no chunking, no ChromaDB, no page refs, no summarisation export |
| 3 | Excel AI | `openpyxl`/pandas parsing, `ExcelService.export` | **Read + export only** — no formula gen/correction, no workbook editing |
| 4 | Word AI | `python-docx` parse + `document_generator` DOCX/PDF export | **Partial** — generation via chat, no dedicated Word workspace |
| 5 | PowerPoint AI | `python-pptx` parse; PPTX not in `GENERATORS` | **Read-only** — no generation |
| 6 | Image gen | `generate_hf_image` (FLUX.1-schnell via HF router) | **Working** |
| 7 | Image edit | Style-transfer Space (optional, env-gated) | **Conditional** — disabled unless `HF_STYLE_TRANSFER_SPACE` set |
| 8 | Pro image editor | — | **Missing** |
| 9 | Video gen (text→video) | `generate_30s_video` (6×5s scenes, HF Space/model) | **Working** when HF creds present |
| 10 | Image→video | Optional HF Space | **Conditional** |
| 11 | Video editing studio | New NLE: `backend/pro_video_editor.py` + `render_pro_video_studio()` | **Working** (colour/transform/mask/overlay/audio/export) |
| 12 | Voice I/O | `st.audio_input` + Gemini audio, browser SpeechSynthesis | **Working** |
| 13 | Admin dashboard | Streamlit admin: users, records, transcripts, role settings | **Partial** (no provider mgmt, no quotas, no credit mgmt) |
| 14 | User mgmt / permissions | `UserFeaturePermission` table + Streamlit settings; FastAPI `/admin/video/users` toggles | **Partial** |
| 15 | Provider/API mgmt | — | **Missing** |
| 16 | Quota/usage mgmt | — | **Missing** |

### 1.3 Two parallel backends — the core architectural fact

There are **two independent user stores**:

1. **MongoDB** (`enterprise_ai_db`): used by *Streamlit* for auth, conversations,
   rooms, profile images. `AUTH_SESSION_COOKIE` based sessions.
2. **SQLAlchemy/SQLite** (`backend_app.db`, `app.models.user.User`): used by *FastAPI*.
   JWT bearer auth, `is_admin`, `UserFeaturePermission`.

Bridging them: FastAPI `mirror_to_sql()` writes a SQL row when a user signs up through
FastAPI, and `deps.get_current_user` reads **only SQLite**. Streamlit reads **only Mongo**.
→ A user created in Streamlit cannot call FastAPI endpoints, and vice-versa, unless
`mirror_to_sql` ran.

This is the single biggest structural issue and it must be resolved before Chat/PDF/
Excel/Word/PPT can share one quota + permission system.

### 1.4 AI layer today

`backend/key_rotator.py` supports **only two providers**:

```python
if provider == "Gemini": ... 16 numbered keys
elif provider == "Hugging Face": ...
else: raise ValueError("Unsupported key-rotation provider")
```

Verified: `Gemini = 16 credentials`, `Hugging Face = 2 credentials`,
`OpenRouter / Groq / Together` → `ValueError`.
Gemini chain: `gemini-3.8-flash → gemini-flash-latest → gemini-3.5-flash →
gemini-3.5-flash-lite → gemini-3.1-flash-lite`.

Gaps vs. the master prompt:
- No provider registry / model registry / router / health monitor.
- No **free-only eligibility** logic (nothing stops a paid model being used).
- No usage tracking or quota enforcement anywhere.
- No mock provider / Development mode.
- No OpenRouter integration at all.

Also broken by inspection: `backend/app/services/ai_provider.py` uses the **OpenAI v0.x API**
(`openai.ChatCompletion.create`), which does not exist in `openai>=1.0`. That provider
class raises at runtime — unused today, but it must not be resurrected as-is.

### 1.5 Frontend / Android reality

- `frontend/package.json` has **no `axios`** (master prompt assumes it), **no `dev` script**,
  and no API layer. Only Login/Register/Dashboard/Admin pages exist.
- No Capacitor config, no Android Gradle project. `mobile_app/` is **Flutter**, not
  Capacitor — that conflicts with the stated stack.
- `frontend` deps: react 19.3, react-router-dom 7.18, tailwind 4.3 (dev only), vite 5.2.

### 1.6 Security observations (code-level)

| Finding | File | Severity |
|---|---|---|
| `JWT_SECRET` defaults to `"super-secret-key"` | `app/core/config.py:18` | **High** — predictable tokens if env missing |
| JWT normally expires in **15 min**, no refresh token; Streamlit has its own 30-day cookie | `app/core/security.py:23` | Medium |
| `get_current_user` trusts `sub` from SQLite only | `app/api/deps.py` | Medium (dual-store) |
| `ai.py` returns `str(exc)` to the client | `endpoints/ai.py:113` | Low (may leak provider detail) |
| Provider keys are plain env vars; no encryption at rest | `key_rotator.py` | Medium (prompt requires encryption) |
| `_jobs` dict for video jobs is in-process only | `endpoints/video.py:28` | Low–Med (lost on restart, memory growth) |
| No per-request rate limiting | all endpoints | Medium |
| `/api/v1/ai/orchestrate` accepts `provider` from client | `endpoints/ai.py:183` | **High** — client can choose provider; violates "backend decides" |

### 1.7 Free-quota risk (highest priority per §14)

- 16 Gemini keys are loaded; nothing prevents a test loop from burning daily quota.
- No mock provider exists → every new test must be written against a local fake.
- No usage log exists, so remaining quota is currently **unknown and unmeasurable**.

---

## 2. The database connectivity blocker (§21) — RESOLVED diagnosis

`tests/mongo_diagnose.py` (read-only, prints no credentials) probes the configured URI
with several auth sources and mechanisms. Latest run on this machine:

- URI scheme: `mongodb+srv`
- Userinfo present: yes
- **`default (as written)` → OK**, `dbs=[...]` returned
- `authSource=admin` → OK
- `authSource=enterprise_ai_db` → OK
- `authMechanism=SCRAM-SHA-256` → OK
- Server reachable inside 6 s; **no writes were attempted.**

Additionally the Streamlit client path was exercised:

```
python -c "import streamlit_app as m; c = m.get_mongo_client(); ..."
→ app Mongo OK, users count returned
```

**Conclusion:** the earlier failure was *environmental* (wrong runtime's `.env`, stale
cached client, or Atlas IP allowlist), not a wrong credential or wrong authSource. The URI
in `backend/.env` authenticates from the normal Atlas auth database. No change to the URI,
no user recreation and no database modification is needed. If it reappears, the failure
will be either (a) the process not loading `backend/.env`, or (b) the machine IP not on the
Atlas allowlist — both are diagnosable with the same script (`TIMEOUT` vs `AUTH-FAIL`).

---

## 3. Conflicts between the master prompt and the existing code

These must be decided **before** large changes; I recommend the left column.

| # | Master prompt says | Code says | Recommended resolution |
|---|---|---|---|
| C1 | React+Vite+Tailwind frontend, Capacitor Android | Working product is **Streamlit**; Flutter app exists; no Capacitor | Keep Streamlit as the shipped web app. Build the React app as the *new* UI on the same FastAPI gateway, feature-by-feature. Do **not** delete Streamlit until parity is proven. Replace Flutter with Capacitor only after approval. |
| C2 | SQLite dev / PostgreSQL prod, JWT | MongoDB is the live store for Streamlit users, chats, rooms | Keep Mongo for existing data. Put **new** quota/usage/provider/admin tables in SQLAlchemy (SQLite→Postgres ready). Bridge with `external_id` mapping. No Mongo writes deleted. |
| C3 | Paid APIs disabled by default | Gemini keys (free tier) are the only working path; OpenRouter absent | Gemini free tier + HF free inference = allowed. Add OpenRouter free-model routing. Add an explicit `PAID_PROVIDERS_ENABLED=false` flag that hard-blocks non-free models. |
| C4 | "Do not create modules unnecessarily" (earlier instruction) | §12–13 ask for a large gateway | Build the gateway **inside `backend/app/services/ai_gateway/`** as a package with small modules, reusing `key_rotator`. No duplicate provider code. |

---

## 4. Implementation plan (phased, each phase independently verifiable)

Every phase ends with: unit tests → mock integration tests → regression check of
Streamlit + FastAPI import → quota-consumption report (must be **0 live calls** unless
explicitly approved).

### Phase 1 — Audit *(this document)*
Done: code audited, architecture documented, DB blocker diagnosed, blockers and conflicts listed.

### Phase 2 — AI Gateway (foundation, no UI change)
New package `backend/app/services/ai_gateway/`:
- `registry.py` — provider + model registry (free/paid classification, capability flags).
- `router.py` — eligibility → permission → quota → pick model → 2–3 fallbacks max.
- `providers/` — `gemini.py`, `huggingface.py`, `openrouter.py`, `mock.py`.
- `quota.py` — atomic reservation (DB row lock), per-chat mode counters, daily counters.
- `usage.py` — `usage_logs` writer, per-provider breakdown.
- `health.py` — availability cache + 429 cooldown (reuses `ProviderRateLimitError`).
- `modes.py` — `DEV` (mock default, live blocked) / `PROD` via `AI_GATEWAY_MODE`.
- `config` — `PAID_PROVIDERS_ENABLED=false`, `TEST_QUOTA_BUDGET_PCT=20`.
Deliverable: `pytest tests/test_ai_gateway.py` — 100 % mocked, **zero live calls**.
Also fix `ai_provider.py`'s dead OpenAI-v0 code path (or delete it — it is unused).

### Phase 3 — Admin/config backend
SQLAlchemy models + migrations: `admin_settings`, `user_quotas`, `usage_logs`,
`credit_transactions`, `media_jobs`, `providers`, `provider_models`, `provider_credentials`
(encrypted with `cryptography.Fernet`, key from env), `audit_logs`.
Endpoints under `/api/v1/admin/`: settings get/update, quotas, providers, health.
Admin UI: extend the existing Streamlit admin (one dashboard, no new module).

### Phase 4 — Chat with modes & quotas
`POST /api/v1/chat/stream` (SSE) through the gateway. Per-chat `high|medium|low` counters
stored on the conversation doc; daily counter on `user_quotas`. Streamlit chat wired to the
gateway (keeping its current UI). Rename/delete/search history.

### Phase 5 — Documents (PDF/Excel/Word/PPT)
`/api/v1/documents/*`: extract → chunk → embed (ChromaDB, optional on-disk) → answer with
page citations; Excel formula gen/fix + workbook edit; Word gen/edit; PPTX generation.
Streamlit tabs per document type.

### Phase 6 — Image studio
Prompt gen/edit via HF free models; local Pillow editor (crop/resize/rotate/flip/
brightness/contrast/saturation/exposure/highlights/shadows/filters/blur/sharpen/undo/redo)
with **no AI call** for basic edits.

### Phase 7 — Video studio hardening
Already functional. Remaining: background job table + status, credit reservation/refund,
upload-size/duration caps, R2 storage adapter, queue limits. Improve the editor (§ below).

### Phase 8 — React UI + Capacitor
Port Streamlit screens to React (Tailwind, axios, router) against the same API; add
Capacitor for Android. Flutter app retired only with approval.

### Phase 9 — QA / production readiness
Full mock QA matrix (§19), security review, backup, monitoring, deployment docs.

---

## 5. Evidence collected (commands actually run)

```
python -c "import streamlit_app"                        → ok
python -m tests.test_pro_video_editor                   → ALL PRO EDITOR TESTS PASSED
python -m tests.test_pro_editor_e2e                     → E2E PRO EDIT PASSED
python tests/mongo_diagnose.py                          → default URI OK, users count > 0
python -c "... key_rotator ..."                         → Gemini 16, HF 2, OpenRouter ValueError
python -c "... importlib.find_spec ..."                  → fastapi/sqlalchemy/motor/chromadb/
                                                          fitz/openpyxl/pptx/pandas present
```

No live AI generation call was made during this audit (quota consumption: **0**).

---

## 6. Immediate blockers & decisions needed from you

1. **UI of record** — Streamlit stays primary while React is built? (recommended: yes)
2. **Android** — Capacitor (as the prompt says) replaces the existing Flutter app, or keep Flutter?
3. **Data store split** — new gateway/quota tables in SQLAlchemy while Mongo keeps chats? (recommended: yes)
4. **OpenRouter** — need an `OPENROUTER_API_KEY` in `.env` (free tier). Without it, routing stays Gemini+HF.
5. **Quota guard** — confirm `AI_GATEWAY_MODE=dev` default (mock providers) for this repo.

Once you confirm 1–5, Phase 2 (AI Gateway, fully mocked tests) starts.
