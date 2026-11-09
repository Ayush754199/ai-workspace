# Deployment

## Pieces

| Component | Port | Notes |
|---|---|---|
| `api` (FastAPI) | 8000 | `backend/Dockerfile`; SQLite on `/data` by default |
| `web` (nginx + Vite build) | 8080 → 80 | `frontend/Dockerfile`; proxies `/api` and `/auth` to the API |

## 1. Configure secrets (never commit them)

`backend/.env` is read at startup. Minimum set:

```dotenv
JWT_SECRET=<48+ random chars>          # required, backend refuses to start without it
ACCESS_TOKEN_EXPIRE_MINUTES=10080      # 7 days

# Free-tier providers only. Numbered keys enable rotation.
GEMINI_KEY_1=...
GROQ_API_KEY_1=gsk_...
OPENROUTER_KEY_1=sk-or-v1-...
OPENROUTER_KEY_2=sk-or-v1-...
HF_TOKEN_1=hf_...

MONGODB_URL=mongodb+srv://...           # optional profile mirror

CORS_ORIGINS=https://app.example.com          # REQUIRED in production (comma separated)
LEGACY_DIRECT_AI_ENABLED=false                  # keep false: old endpoints bypass quota
AI_GATEWAY_MODE=live                            # dev = mock provider only
AI_DAILY_QUOTA=200                      # per-user default; admins can override per user
AI_PER_CHAT_QUOTA=40
AI_RATE_LIMIT_PER_MINUTE=30
AUTH_RATE_LIMIT_PER_MINUTE=10
```

Generate a secret: `python -c "import secrets;print(secrets.token_urlsafe(48))"`.

`backend/.env`, `frontend/.env*` and `*.db` are ignored by git (see `.gitignore`).
Rotate any key that has ever been pasted into chat, logs or a commit.

## 2. Run

```bash
docker compose up -d --build
# web: http://localhost:8080   api docs: http://localhost:8000/docs
```

Or manually:

```bash
cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000
cd frontend && VITE_API_BASE_URL=https://api.example.com npm run build   # serve dist/ with nginx
```

## 2a. Render (hosted)

Render has no `.env` file -- `backend/.env` is gitignored and never shipped. Every
value from step 1 must exist in **Dashboard -> your service -> Environment**.

The repository ships `render.yaml`, so pressing **New -> Blueprint** creates the
service with `JWT_SECRET` auto-generated. If the service already exists, add the
variables by hand.

Missing `JWT_SECRET` is the most common first-deploy failure. The app now fails
with an explicit message instead of a raw pydantic error:

```
RuntimeError: JWT_SECRET is missing or too short (minimum 32 characters).
Local:   set it in backend/.env (see backend/.env.example).
Render:  Dashboard -> your service -> Environment -> add JWT_SECRET.
Generate: python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Create a value with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Notes for the free plan:

- The container filesystem is ephemeral. SQLite (`backend_app.db`) is reset on
  every deploy -- set `DATABASE_URL` to a managed Postgres to keep data.
- `AI_GATEWAY_MODE=dev` keeps the mock provider; switch to `live` only after a
  provider key is added.

---

The first registered user is **not** an admin automatically. Promote one:

```bash
python -c "import sqlite3;c=sqlite3.connect('backend_app.db');c.execute(\"update users set is_admin=1 where email='you@example.com'\");c.commit()"
```

## 3. Provider enablement (no surprise bills)

* The gateway only considers providers in `FREE_PROVIDERS` (`gemini`, `groq`,
  `openrouter`, `huggingface`) **and** models an admin marked
  `enabled + is_free + availability=available + verified_at` in
  *Admin → Providers & models*. Paid providers cannot be enabled from the UI.
* Image-to-video / image editing remain disabled until `HF_IMAGE_TO_VIDEO_SPACE` /
  `HF_IMAGE_EDIT_SPACE` are set.
* Answer "not configured"/503 messages are intentional: there is no dummy success.

## 4. Logging & monitoring

* App logs go to stdout (`docker logs -f ai-workspace-api`). Provider failures are
  written to `backend/system.log` with key ids only (never key values).
* `GET /api/v1/health` is the container health check; wire it to your uptime monitor.
* `GET /api/v1/admin/analytics/usage?days=7` and `GET /api/v1/admin/logs` expose
  real usage; every request also carries `X-Request-Id` for correlation.
* Alert on: repeated 429s from providers, `X-Request-Id` 5xx bursts, quota
  exhaustion (`QuotaExhausted` in logs).

## 5. Backups

`scripts/backup.sh` writes a verified, gzipped SQLite backup and keeps 14 days.
Cron example:

```
15 2 * * * DB=/data/app.db BACKUP_DIR=/var/backups/ai-workspace /app/scripts/backup.sh
```

Restore: stop the stack, `gunzip` the archive to `/data/app.db`, start again.
For Postgres set `DATABASE_URL=postgresql+psycopg://...` and use `pg_dump`.

## 6. Scaling notes

The rate limiter and per-user concurrency guard are in-process, so run **one**
uvicorn worker (the Dockerfile does). For multiple instances:
put `limit_req`/`limit_conn` at nginx (already configured for `/auth/`), and move
quota reservations to Postgres (already transactional) before scaling past one
writer per SQLite file.

## 7. Pre-release checklist

- [ ] `JWT_SECRET` set and unique per environment
- [ ] `.env` files not tracked (`git status --ignored`)
- [ ] `cd backend && pytest -q` green
- [ ] `cd frontend && npx vitest run && npm run build` green
- [ ] Admin: only intended providers/models enabled & verified
- [ ] Backup job scheduled and one restore tested
- [ ] HTTPS terminated at the proxy; `X-Forwarded-For` passed through
