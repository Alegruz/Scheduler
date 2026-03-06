from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    APP_NAME: str = "Life Scheduler"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = "postgresql://scheduler:scheduler@localhost:5432/scheduler"

    # Security
    SECRET_KEY: str = "change-me-in-production-use-a-long-random-string"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h

    # Google Calendar OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/google/callback"

    # Scheduler
    FREEZE_HORIZON_MINUTES: int = 30  # blocks starting within 30min are frozen
    NIGHTLY_PLAN_HOUR: int = 22  # run nightly planning at 22:00 local time
    INTRADAY_REPAIR_INTERVAL_MINUTES: int = 15

    # Notifications
    FCM_SERVER_KEY: str = ""


@lru_cache()
def get_settings() -> Settings:
    return Settings()
