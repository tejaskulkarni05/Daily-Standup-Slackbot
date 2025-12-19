"""Configuration module with Pydantic BaseSettings."""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Slack Configuration
    slack_bot_token: str = Field(..., alias="SLACK_BOT_TOKEN")
    slack_signing_secret: str = Field(..., alias="SLACK_SIGNING_SECRET")
    slack_default_channel: str = Field(..., alias="SLACK_DEFAULT_CHANNEL")

    # Database Configuration
    database_url: str = Field(
        "postgresql+asyncpg://standup_user:standup_password@localhost:5432/standup_db",
        alias="DATABASE_URL",
    )

    # Scheduler Configuration
    default_standup_time: str = Field("09:30", alias="DEFAULT_STANDUP_TIME")
    scheduler_timezone: str = Field("Asia/Kolkata", alias="SCHEDULER_TIMEZONE")

    # Application Configuration
    env: str = Field("dev", alias="ENV")  # dev or prod
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    admin_token: str = Field("", alias="ADMIN_TOKEN")

    # Feature Flags
    skip_notification_to_channel: bool = Field(False, alias="SKIP_NOTIFICATION_TO_CHANNEL")

    class Config:
        """Pydantic config."""

        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()
