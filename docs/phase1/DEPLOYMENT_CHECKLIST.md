# Deployment checklist (Phase 1)

Exact environment variables and commands for the updated application.
No secret value is shown; `sync: false` in `render.yaml` means "set it in the
Render dashboard, never in git".

---

## 1. Render (API service created from `render.yaml`)

| Setting | Value |
|---|---|
| Root directory | `backend` |
| Build command | `pip install -r requirements.txt` (installs `backend/requirements.txt`) |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --log-level info --proxy-headers --forwarded-allow-ips="127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"` |
| Health check | `/api/v1/health` |
| Region / plan | `singapore` / `free` |

### Required environment variables

| Variable | Example / format | Notes |
|---|---|---|
| `ENVIRONMENT` | `production` | A missing/short `JWT_SECRET` is a **startup error** in production. |
| `JWT_SECRET` | 48+ random chars | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `DATABASE_URL` | injected from `ai-workspace-db` | Managed Postgres; `postgres://` is rewritten to `postgresql+psycopg://`. |
| `CORS_ORIGINS` | `https://<frontend-host>` | Comma-separated; required in production. |
| `TRUSTED_PROXY_CIDRS` | `127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` | Only these peers may set `X-Forwarded-For`. |
| `AI_GATEWAY_MODE` | `live` | `dev` routes every chat call to the mock provider. |

### One provider is enough to start chatting

Set **one** of these and the gateway works on a fresh database (no `providers` /
`provider_models` rows required):

| Variable | Provider | Model variable |
|---|---|---|
| `OPENROUTER_KEY_1` (+ `_2`, `_3` to rotate) | OpenRouter (initial provider) | `OPENROUTER_MODEL=openrouter/free` |
| `GEMINI_KEY_1` (or `GEMINI_API_KEY`) | Google AI Studio | `GEMINI_MODEL` (default `gemini-3.8-flash`) |
| `GROQ_API_KEY_1` **and** `GROQ_MODEL` | Groq | `GROQ_MODEL` has no default — set an id you verified on your account |

### Optional variables

| Variable | Purpose |
|---|---|
| `MONGODB_URL`, `MONGODB_DATABASE` | Optional legacy profile/call mirror. Startup continues in SQL-only mode if Atlas is unreachable; logins still work. |
| `ADMIN_BOOTSTRAP_IDENTIFIER` | Email/mobile/identifier of an **existing** account promoted to admin at startup. Remove it after verifying. Registration never creates an admin. |
| `API_DOCS_ENABLED=false` | Keeps `/docs`, `/redoc`, `/openapi.json` off. |
| `HF_TOKEN`, `CLOUDFLARE_*`, `LIVEKIT_*`, `TAVILY_API_KEY_*` | Feature-specific; the feature answers 503 until configured. |
| `ATTACHMENT_STORAGE_DIR` | **Only when a disk is mounted.** See the storage note below. |

### Storage on Render

Render's container filesystem is **ephemeral**. On the **free plan a disk cannot be
mounted**, so uploaded attachments and generated Office files are lost on every
deploy/restart. `render.yaml` therefore does **not** set `ATTACHMENT_STORAGE_DIR`
for the free plan.

To keep uploads:

1. Change the service to a paid instance type.
2. Uncomment the `disk:` block in `render.yaml` (`mountPath: /var/data`, `sizeGB: 1`).
3. Add `ATTACHMENT_STORAGE_DIR=/var/data/attachments`.

### Administrator provisioning

```text
1. The user registers normally (they are always a plain user).
2. Render -> Environment -> ADMIN_BOOTSTRAP_IDENTIFIER = <their email/mobile>
3. Manual Deploy (or wait for the restart): startup logs "Administrator provisioned from ADMIN_BOOTSTRAP_IDENTIFIER: <id>".
4. Verify /api/v1/admin/... works, then DELETE the variable and redeploy.
```

Existing administrators keep their flag. Re-running the bootstrap never re-grants
rights an administrator has since revoked, and never demotes anybody.

---

## 2. Docker (local / self-hosted)

```bash
cp backend/.env.example backend/.env      # then fill in JWT_SECRET + one provider key
docker compose config                      # validates the compose file
docker compose up --build -d
docker compose logs -f api
curl http://localhost:8000/api/v1/health   # {"status":"ok"}
```

The compose file sets `ENVIRONMENT=production`, `DATABASE_URL=sqlite:////data/app.db`
and `ATTACHMENT_STORAGE_DIR=/data/attachments`, all persisted on the `api-data`
volume, so uploads and the database survive `docker compose down` / `up`.

The image runs uvicorn with `--forwarded-allow-ips 172.16.0.0/12,192.168.0.0/16,127.0.0.1`
(the compose bridge and loopback), matching `TRUSTED_PROXY_CIDRS`.

---

## 3. Local development

```powershell
cd backend
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
# or from the repo root (same module):
python -m uvicorn backend.app.main:app --reload --port 8000
```

`ENVIRONMENT` defaults to `development`, where a missing `JWT_SECRET` is replaced by
a random per-process key and a loud log line (users are signed out on restart).

Frontend:

```powershell
cd frontend
npm install
npm run test -- --run
npm run build      # production bundle in frontend/dist (served by the API at /)
```

---

## 4. Post-deploy smoke test

| # | Check | Command |
|---|---|---|
| 1 | Health | `curl -s $URL/api/v1/health` → `{"status":"ok"}` |
| 2 | Register | `POST /auth/register` → 200, `is_admin: false` |
| 3 | Login | `POST /auth/login` → 200 + `access_token` |
| 4 | Session | `GET /auth/me` → 200 |
| 5 | Provider routing | `GET /api/v1/chat/v2/models` → `auto.selectable: true` |
| 6 | Chat | `POST /api/v1/chat/v2/conversations` then `.../messages` → 200 |
| 7 | Streaming | `POST .../stream` → `data: {"chunk": ...}` … `data: [DONE]` |
| 8 | Upload/download | `POST /api/v1/chat/v2/attachments`, then `GET .../download` |
| 9 | Admin | `GET /api/v1/admin/providers` with the provisioned admin |

### Phase 1 verification record (local dev / SQLite)

The standalone `backend/_phase1_smoke.py` was run against the development-mode,
SQLite configuration with MongoDB disabled. Health, registration, login, session,
and model discovery each returned HTTP 200; registration returned
`is_admin: false`, and the `auto` model was selectable. This is a local smoke test,
not a production deployment test. Chat generation, streaming, attachment
upload/download, and the admin endpoint were not exercised by this smoke script;
use the table above to verify those against the target deployment.

---

## 5. Do not do

* Do not commit `backend/.env` or any `*.env` other than `*.env.example`.
* Do not run `git filter-branch` / `filter-repo` or force-push as part of a deploy.
* Do not trust historical secrets: rotate them (see `docs/phase1/SECRET_ROTATION.md`).
