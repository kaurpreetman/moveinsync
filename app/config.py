import os


class Settings:
    """Application settings loaded from environment variables."""

    JWT_SECRET: str = os.getenv(
        "JWT_SECRET", "moveinsync-dev-secret-key-do-not-use-in-prod"
    )
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_MINUTES: int = int(os.getenv("JWT_EXPIRY_MINUTES", "480"))
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    DB_PATH: str = os.getenv("DB_PATH", "data/moveinsync.db")
    DEV_AUTH_ENABLED: bool = os.getenv("DEV_AUTH_ENABLED", "true").lower() == "true"
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini-2.0-flash")
    MAX_LLM_CALLS: int = min(max(int(os.getenv("MAX_LLM_CALLS", "4")), 1), 4)
    LLM_TIMEOUT_SECONDS: float = min(
        max(float(os.getenv("LLM_TIMEOUT_SECONDS", "2.5")), 0.1), 2.5
    )
    DB_BUSY_TIMEOUT_SECONDS: float = min(
        max(float(os.getenv("DB_BUSY_TIMEOUT_SECONDS", "0.5")), 0.05), 0.5
    )


settings = Settings()
