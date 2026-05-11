from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    API_HOST: str = Field(default="0.0.0.0")
    API_PORT: int = Field(default=8000)

    TELEGRAM_BOT_TOKEN: str = Field(default="")
    TELEGRAM_DEFAULT_CHAT_ID: str = Field(default="")
    TELEGRAM_PARSE_MODE: str = Field(default="HTML")
    TELEGRAM_PROXY_URL: str = Field(default="")

    CLICKHOUSE_HOST: str = Field(default="clickhouse-db")
    CLICKHOUSE_PORT: int = Field(default=8123)
    CLICKHOUSE_DATABASE: str = Field(default="crypto_data")
    CLICKHOUSE_USER: str = Field(default="default")
    CLICKHOUSE_PASSWORD: str = Field(default="")

    RATE_LIMIT_CAPACITY: int = Field(default=5)
    RATE_LIMIT_REFILL_PER_SEC: float = Field(default=1.0)

    LOG_LEVEL: str = Field(default="INFO")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()
