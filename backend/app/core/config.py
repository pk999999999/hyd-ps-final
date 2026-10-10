"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path
from pydantic import Field, model_validator
from typing import Literal


class Settings(BaseSettings):
    # ── App ──
    app_name: str = "hyd-ps2"
    app_env: str = "development"
    debug: bool = True
    demo_mode: bool = False

    # ── Auth ──
    jwt_secret: str = "CHANGE_ME"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # ── PostgreSQL ──
    database_url: str = "postgresql+asyncpg://hydps2:CHANGE_ME@localhost:5432/hydps2"

    # ── MongoDB ──
    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "hydps2_memory"

    # ── Redis ──
    redis_url: str = "redis://localhost:6379/0"

    # ── LLM ──
    groq_api_key: str = ""
    gemini_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-oss-120b"
    llm_provider: Literal["auto", "openrouter", "groq", "gemini", "openai", "anthropic", "compatible"] = "auto"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = ""
    groq_model: str = "llama-3.1-8b-instant"
    gemini_model: str = "gemini-2.5-flash"

    # ── ElevenLabs ──
    elevenlabs_api_key: str = ""
    elevenlabs_agent_id: str = ""
    elevenlabs_operator_agent_id: str = ""
    elevenlabs_webhook_secret: str = ""

    # ── Email ──
    resend_api_key: str = ""
    resend_webhook_secret: str = ""
    receiving_domain: str = ""
    from_email: str = "noreply@yourdomain.com"
    email_from: str = "noreply@yourdomain.com"
    public_base_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    email_enabled: bool = False
    email_provider: Literal["smtp", "resend"] = "smtp"
    smtp_host: str = "mailhog"
    smtp_port: int = 1025

    # ── SMS (Textbee Android gateway) ──
    textbee_api_key: str = ""
    textbee_base_url: str = "https://api.textbee.dev/api/v1"
    textbee_device_id: str = ""
    textbee_webhook_secret: str = ""
    sms_enabled: bool = False
    daily_sms_cap: int = 20

    # ── Telegram ──
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_enabled: bool = False

    # ── Scraper ──
    scraper_base_url: str = "http://localhost:8080"
    tavily_api_key: str = ""
    lead_score_record_weight: int = Field(5, ge=0, le=100)
    lead_score_email_weight: int = Field(15, ge=0, le=100)
    lead_score_phone_weight: int = Field(10, ge=0, le=100)
    lead_score_website_weight: int = Field(10, ge=0, le=100)
    lead_score_maps_weight: int = Field(10, ge=0, le=100)
    lead_score_observed_website_weight: int = Field(15, ge=0, le=100)
    lead_score_matched_search_weight: int = Field(10, ge=0, le=100)
    lead_score_profile_weight: int = Field(10, ge=0, le=100)
    lead_score_target_fit_weight: int = Field(15, ge=0, le=100)

    # ── Send caps ──
    daily_email_cap: int = 50
    daily_send_cap: int = 50
    worker_concurrency: int = Field(3, ge=1, le=16)
    worker_poll_seconds: float = 2.0
    cache_ttl_seconds: int = 900
    llm_timeout_seconds: float = 45.0
    llm_max_output_tokens: int = Field(640, ge=128, le=8000)
    llm_reasoning_effort: str = "minimal"

    @model_validator(mode="after")
    def validate_demo_isolation(self):
        if self.demo_mode:
            database_name = self.database_url.split("?", 1)[0].rsplit("/", 1)[-1]
            if (self.app_env != "demo" or not database_name.endswith("_demo")
                    or not self.mongo_db.endswith("_demo") or self.email_provider != "smtp"
                    or self.email_enabled):
                raise ValueError("DEMO_MODE requires APP_ENV=demo, separate *_demo SQL/Mongo databases, and MailHog-only email.")
        return self

    model_config = {"env_file": (Path(__file__).resolve().parents[3] / ".env", ".env"), "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
