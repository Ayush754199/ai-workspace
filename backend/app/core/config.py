from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "AI Workspace Backend"
    JWT_SECRET: str = "super-secret-key"
    ALGORITHM: str = "HS256"
    GEMINI_API_KEY: str = "default-test-key"
    MONGODB_URL: str = ""
    MONGODB_DATABASE: str = "ai_workspace"

    class Config:
        env_file = ".env"

settings = Settings()