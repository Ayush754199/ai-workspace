from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "AI Workspace Backend"
    JWT_SECRET: str = "super-secret-key"
    ALGORITHM: str = "HS256"
    # Google AI Studio keys have multiple valid formats; do not restrict by prefix.
    GEMINI_API_KEY: str = Field(default="default-test-key", min_length=1)
    MONGODB_URL: str = ""
    MONGODB_DATABASE: str = "ai_workspace"

settings = Settings()