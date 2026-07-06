from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Database Settings
    DATABASE_URL: str = "sqlite+aiosqlite:////home/airc/agent-service/agent.db"

    # Redis Settings
    REDIS_URL: str = "redis://localhost:6379/0"

    # LLM Settings (Can be mocked or real OpenAI/Anthropic compat API)
    LLM_API_KEY: str = "mock-key-for-testing"
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4-turbo"

    # Agent Constraints
    MAX_REACT_STEPS: int = 10
    TOOL_TIMEOUT: float = 30.0
    GLOBAL_TIMEOUT: float = 120.0

    # Rate Limiting
    RATE_LIMIT_CALLS: int = 60
    RATE_LIMIT_PERIOD: int = 60  # in seconds


settings = Settings()
