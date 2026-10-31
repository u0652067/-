"""Application settings, loaded only from environment variables / .env.

No secrets are ever hard-coded here. See `.env.example` at the repo root.
"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AQT_", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://postgres@127.0.0.1:5432/aqt",
        description="SQLAlchemy URL of the PostgreSQL database",
    )
    # Trading mode is hard-wired: this codebase contains no live-broker code path.
    trading_mode: str = "SIMULATION"

    # HTTP behaviour toward public data sources (be polite: TWSE bans aggressive scrapers)
    http_timeout_seconds: float = 30.0
    http_min_interval_seconds: float = 5.0  # min gap between requests to the same host
    http_max_retries: int = 3
    http_user_agent: str = "ai-quant-trader/0.1 (research; simulation only)"

    # Optional third-party sources
    finmind_token: str | None = None
    anthropic_api_key: str | None = None

    # Data-quality / liquidity thresholds used by the universe filter
    min_avg_turnover_twd: float = 10_000_000  # 20-day average traded value
    min_price_twd: float = 10.0
    stale_data_max_age_hours: float = 36.0

    timezone: str = "Asia/Taipei"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.trading_mode != "SIMULATION":
        raise RuntimeError("Only SIMULATION trading mode is permitted in this codebase.")
    return s
