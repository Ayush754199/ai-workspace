import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.ai import router as ai_router
from app.api.v1.endpoints.chat import router as chat_router
from app.api.v1.endpoints.admin import router as admin_router
from app.api.v1.endpoints.rooms import router as rooms_router
from app.api.v1.endpoints.video import router as video_router
from app.api.v1.endpoints.chat_v2 import router as chat_v2_router
from app.api.v1.endpoints.documents import router as documents_router
from app.api.v1.endpoints.images import router as images_router
from app.api.v1.endpoints.office import router as office_router
from app.api.v1.endpoints.research import router as research_router
from app.api.v1.endpoints.voice import router as voice_router
from app.core.rate_limit import SecurityMiddleware
from sqlalchemy import inspect, text
from app.core.database import Base, engine
from app.core.config import settings
from app.db.session import mongo
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(_: FastAPI):
	try:
		yield
	finally:
		await mongo.close()

app = FastAPI(
	title="Enterprise AI Workspace Backend",
	docs_url="/docs" if settings.API_DOCS_ENABLED else None,
	redoc_url="/redoc" if settings.API_DOCS_ENABLED else None,
	openapi_url="/openapi.json" if settings.API_DOCS_ENABLED else None,
	lifespan=lifespan,
)

app.include_router(health_router, prefix="/api/v1", tags=["health"])
app.include_router(auth_router, tags=["auth"])
app.include_router(auth_router, prefix="/api/v1", tags=["auth"])
app.include_router(ai_router, prefix="/api/v1", tags=["ai"])
app.include_router(chat_router, prefix="/api/v1", tags=["chat"])
app.include_router(admin_router, prefix="/api/v1", tags=["admin"])
app.include_router(rooms_router, prefix="/api/v1", tags=["rooms"])
app.include_router(video_router, prefix="/api/v1", tags=["video"])
app.include_router(chat_v2_router, prefix="/api/v1", tags=["chat"])
app.include_router(documents_router, prefix="/api/v1", tags=["documents"])
app.include_router(images_router, prefix="/api/v1", tags=["images"])
app.include_router(office_router, prefix="/api/v1", tags=["office"])
app.include_router(research_router, prefix="/api/v1", tags=["research"])
app.include_router(voice_router, prefix="/api/v1", tags=["voice"])
app.add_middleware(SecurityMiddleware)

# The frontend runs on a different origin (Vite dev server / static host), so the
# browser blocks fetch() without CORS headers. Bearer tokens are used instead of
# cookies, so credentials do not need to be shared across origins.
# Wildcard is only used when explicitly allowed (dev); production should list
# its real origins in CORS_ORIGINS so a random site cannot script the API with a
# stolen token.
_allowed_origins = [origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()]
if not _allowed_origins and settings.AI_GATEWAY_MODE.lower() not in {"live", "production"}:
	_allowed_origins = ["*"]
app.add_middleware(
	CORSMiddleware,
	allow_origins=_allowed_origins,
	allow_credentials=False,
	allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
	allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-Id"],
	expose_headers=["Content-Disposition", "X-Request-Id", "X-TTS-Engine"],
	max_age=600,
)


@app.get("/")
def root():
	return {"message": "Enterprise AI Workspace Backend is Running!"}


# The React build uses BrowserRouter. Serving the built SPA from the API means a
# deep link such as /dashboard or /login must not 404 the way a static host would.
_FRONTEND_DIST = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../frontend/dist"))

# The API surface. Everything the React client calls lives under /api/v1, plus the
# legacy /auth/* and /chat/* aliases; an unknown route under any of these is a real
# 404 and must never fall through to the SPA shell. Returning index.html instead
# made a typo'd endpoint look like a healthy 200 to clients, and let scanners that
# probe /.env or /docs see a fake success.
# Bare page names (/documents, /images, /office, /research, /video-studio) are NOT
# API roots -- they belong to the router below.
_API_PREFIXES = ("api", "auth", "chat", "rooms")
# Server metadata and secret-bearing file names that must never be served.
_BLOCKED_SEGMENTS = {"docs", "redoc", "openapi.json", "api-docs"}
# A relative asset (scripts, sourcemaps, fonts) must not be answered with HTML.
_ASSET_EXTENSIONS = {
	".js", ".mjs", ".css", ".map", ".json", ".png", ".jpg", ".jpeg", ".gif", ".svg",
	".webp", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".webm", ".txt", ".xml",
}
# The exact client-side routes this server may answer with the SPA shell
# (frontend/src/App.tsx). Keep in sync when a page is added.
_SPA_PATHS = {
	"login", "register", "dashboard", "assistant", "documents", "images", "office",
	"research", "chat", "video-studio", "video-studio/editor", "profile", "admin",
}
# The exact nested client routes (see frontend/src/App.tsx). An unknown child such
# as /video-studio/nope must 404 rather than render the app shell.
_SPA_NESTED_PATHS = {"video-studio/editor"}

def _is_api_or_reserved_path(full_path: str) -> bool:
	"""True when the path belongs to the API/metadata surface, not the SPA."""
	segments = [segment for segment in full_path.split("/") if segment]
	if not segments:
		return True
	if segments[0].lower() in _API_PREFIXES or segments[0].lower() in _BLOCKED_SEGMENTS:
		return True
	# Any dotfile segment (.env, .git, .htaccess) is a secret probe.
	if any(segment.startswith(".") for segment in segments):
		return True
	return False


@app.get("/{full_path:path}", include_in_schema=False)
def serve_frontend(full_path: str):
	segments = [segment for segment in full_path.split("/") if segment]
	if _is_api_or_reserved_path(full_path):
		raise HTTPException(status_code=404, detail="Not Found")
	if not os.path.isdir(_FRONTEND_DIST):
		raise HTTPException(status_code=404, detail="Not Found")
	candidate = os.path.abspath(os.path.join(_FRONTEND_DIST, full_path))
	if segments and candidate.startswith(_FRONTEND_DIST) and os.path.isfile(candidate):
		return FileResponse(candidate)
	# A missing asset is 404, never the HTML shell (a browser would execute it).
	if os.path.splitext(segments[-1])[1].lower() in _ASSET_EXTENSIONS:
		raise HTTPException(status_code=404, detail="Not Found")
	# Only the declared client-side routes get the SPA entrypoint; everything else
	# is a genuine 404, including unknown children of a real route.
	route = "/".join(segment.lower() for segment in segments)
	if route in _SPA_PATHS:
		return FileResponse(os.path.join(_FRONTEND_DIST, "index.html"))
	raise HTTPException(status_code=404, detail="Not Found")


def ensure_schema():
	"""Create missing tables and keep existing SQLite installations compatible.

	Runs at import time, not on a startup hook: endpoints fall back to SQLite when
	Mongo has no matching record, so the schema must exist even if the ASGI lifespan
	never fires (scripts, test clients, serverless handlers).
	"""
	Base.metadata.create_all(bind=engine)
	inspector = inspect(engine)
	if "users" in inspector.get_table_names():
		columns = {column["name"] for column in inspector.get_columns("users")}
		if "is_admin" not in columns:
			with engine.begin() as connection:
				connection.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"))
		if "identifier" not in columns:
			with engine.begin() as connection:
				connection.execute(text("ALTER TABLE users ADD COLUMN identifier VARCHAR"))
				connection.execute(text("UPDATE users SET identifier = email WHERE identifier IS NULL"))
		if "dob" not in columns:
			with engine.begin() as connection:
				connection.execute(text("ALTER TABLE users ADD COLUMN dob VARCHAR"))
		if "mongo_id" not in columns:
			with engine.begin() as connection:
				connection.execute(text("ALTER TABLE users ADD COLUMN mongo_id VARCHAR"))
		if "mobile_number" not in columns:
			with engine.begin() as connection:
				connection.execute(text("ALTER TABLE users ADD COLUMN mobile_number VARCHAR(32)"))
	if "user_quotas" in inspector.get_table_names():
		quota_columns = {column["name"] for column in inspector.get_columns("user_quotas")}
		with engine.begin() as connection:
			if "per_chat_limit" not in quota_columns:
				connection.execute(text("ALTER TABLE user_quotas ADD COLUMN per_chat_limit INTEGER NOT NULL DEFAULT 20"))
			if "daily_window" not in quota_columns:
				connection.execute(text("ALTER TABLE user_quotas ADD COLUMN daily_window DATE"))
				connection.execute(text("UPDATE user_quotas SET daily_window = DATE('now') WHERE daily_window IS NULL"))
	if "usage_logs" in inspector.get_table_names():
		usage_columns = {column["name"] for column in inspector.get_columns("usage_logs")}
		with engine.begin() as connection:
			if "reservation_id" not in usage_columns:
				connection.execute(text("ALTER TABLE usage_logs ADD COLUMN reservation_id VARCHAR(80)"))
			if "request_id" not in usage_columns:
				connection.execute(text("ALTER TABLE usage_logs ADD COLUMN request_id VARCHAR(160)"))
	if "quota_reservations" in inspector.get_table_names():
		reservation_columns = {column["name"] for column in inspector.get_columns("quota_reservations")}
		if "capability" not in reservation_columns:
			with engine.begin() as connection:
				connection.execute(text("ALTER TABLE quota_reservations ADD COLUMN capability VARCHAR(40) NOT NULL DEFAULT 'chat'"))
	if "provider_models" in inspector.get_table_names():
		model_columns = {column["name"] for column in inspector.get_columns("provider_models")}
		with engine.begin() as connection:
			if "availability" not in model_columns:
				connection.execute(text("ALTER TABLE provider_models ADD COLUMN availability VARCHAR(40) NOT NULL DEFAULT 'unknown'"))
			if "verified_at" not in model_columns:
				connection.execute(text("ALTER TABLE provider_models ADD COLUMN verified_at DATETIME"))
			if "max_input_tokens" not in model_columns:
				connection.execute(text("ALTER TABLE provider_models ADD COLUMN max_input_tokens INTEGER"))
			if "max_output_tokens" not in model_columns:
				connection.execute(text("ALTER TABLE provider_models ADD COLUMN max_output_tokens INTEGER"))
			if "restrictions" not in model_columns:
				connection.execute(text("ALTER TABLE provider_models ADD COLUMN restrictions JSON NOT NULL DEFAULT '[]'"))
	if "rooms" in inspector.get_table_names():
		room_columns = {column["name"] for column in inspector.get_columns("rooms")}
		with engine.begin() as connection:
			if "is_pinned" not in room_columns:
				connection.execute(text("ALTER TABLE rooms ADD COLUMN is_pinned BOOLEAN NOT NULL DEFAULT 0"))
			if "is_archived" not in room_columns:
				connection.execute(text("ALTER TABLE rooms ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT 0"))
	if "room_invites" in inspector.get_table_names():
		invite_columns = {column["name"] for column in inspector.get_columns("room_invites")}
		with engine.begin() as connection:
			if "invited_user_id" not in invite_columns:
				connection.execute(text("ALTER TABLE room_invites ADD COLUMN invited_user_id INTEGER"))
			if "status" not in invite_columns:
				connection.execute(text("ALTER TABLE room_invites ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'pending'"))
	if "media_jobs" in inspector.get_table_names():
		media_job_columns = {column["name"] for column in inspector.get_columns("media_jobs")}
		with engine.begin() as connection:
			if "reservation_id" not in media_job_columns:
				connection.execute(text("ALTER TABLE media_jobs ADD COLUMN reservation_id VARCHAR(80)"))
			if "worker_id" not in media_job_columns:
				connection.execute(text("ALTER TABLE media_jobs ADD COLUMN worker_id VARCHAR(80)"))
			if "heartbeat_at" not in media_job_columns:
				connection.execute(text("ALTER TABLE media_jobs ADD COLUMN heartbeat_at DATETIME"))
	if "ai_attachments" in inspector.get_table_names():
		attachment_columns = {column["name"] for column in inspector.get_columns("ai_attachments")}
		if "storage_path" not in attachment_columns:
			with engine.begin() as connection:
				connection.execute(text("ALTER TABLE ai_attachments ADD COLUMN storage_path VARCHAR(400)"))
	message_columns = {column["name"] for column in inspector.get_columns("messages")} if "messages" in inspector.get_table_names() else set()
	if "mention_user_ids" not in message_columns and "messages" in inspector.get_table_names():
		with engine.begin() as connection:
			connection.execute(text("ALTER TABLE messages ADD COLUMN mention_user_ids JSON NOT NULL DEFAULT '[]'"))
	bootstrap_identifier = settings.ADMIN_BOOTSTRAP_IDENTIFIER.strip()
	if bootstrap_identifier and "users" in inspector.get_table_names():
		with engine.begin() as connection:
			connection.execute(
				text(
					"UPDATE users SET is_admin = TRUE, role = 'admin' "
					"WHERE mobile_number = :identifier OR identifier = :identifier OR email = :identifier"
				),
				{"identifier": bootstrap_identifier},
			)


ensure_schema()


@app.on_event("startup")
def ensure_user_admin_column():
	"""Re-run schema compatibility and recover interrupted media jobs."""
	ensure_schema()
	from app.api.v1.endpoints.video import recover_interrupted_jobs
	recover_interrupted_jobs()

# Protected route example - all endpoints will depend on get_current_user
# This can be applied globally or per-endpoint
