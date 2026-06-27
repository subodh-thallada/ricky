from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Bench Orchestrator"
    primary_llm_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_model: str = "gemini-2.5-flash"
    cerebras_api_key: str = ""
    cerebras_base_url: str = "https://api.cerebras.ai/v1"
    cerebras_model: str = "gpt-oss-120b"
    backboard_api_key: str = ""
    backboard_llm_provider: str = "openrouter"
    backboard_model_name: str = "moonshotai/kimi-k2.6"
    backboard_assistant_name: str = "Bench Preference Memory"


@lru_cache
def get_settings() -> Settings:
    return Settings()
