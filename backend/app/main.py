from fastapi import FastAPI
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.ai import router as ai_router
from app.api.v1.endpoints.admin import router as admin_router
from app.api.v1.endpoints.rooms import router as rooms_router
from app.api.v1.endpoints.chat import router as chat_router
from sqlalchemy import inspect, text
from app.core.database import Base, engine

app = FastAPI()

app.include_router(health_router, prefix="/api/v1", tags=["health"])
app.include_router(auth_router, tags=["auth"])
app.include_router(auth_router, prefix="/api/v1", tags=["auth"])
app.include_router(ai_router, prefix="/api/v1", tags=["ai"])
app.include_router(admin_router, prefix="/api/v1", tags=["admin"])
app.include_router(rooms_router, prefix="/api/v1", tags=["rooms"])
app.include_router(chat_router, prefix="/api/v1", tags=["chat"])


@app.get("/")
def root():
	return {"message": "Enterprise AI Workspace Backend is Running!"}


@app.on_event("startup")
def ensure_user_admin_column():
	"""Keep existing SQLite installations compatible with the admin flag."""
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
	message_columns = {column["name"] for column in inspector.get_columns("messages")} if "messages" in inspector.get_table_names() else set()
	if "mention_user_ids" not in message_columns and "messages" in inspector.get_table_names():
		with engine.begin() as connection:
			connection.execute(text("ALTER TABLE messages ADD COLUMN mention_user_ids JSON NOT NULL DEFAULT '[]'"))
	Base.metadata.create_all(bind=engine)

# Protected route example - all endpoints will depend on get_current_user
# This can be applied globally or per-endpoint