from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 基础应用配置
    APP_NAME: str = "OmniDigest"
    APP_ENV: str = "development"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    API_KEY: Optional[str] = None

    # LLM 与推理配置 (兼容 OpenAI 规范)
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: str = "https://api.deepseek.com/v1"
    MODEL_NAME: str = "deepseek-chat"
    TEMPERATURE: float = 0.3
    EMBEDDING_MODEL_NAME: str = "text-embedding-3-small"

    # 联网搜索核查配置 (Tavily 或 DuckDuckGo)
    TAVILY_API_KEY: Optional[str] = None

    # 存储与数据库配置
    # 支持 PostgreSQL+asyncpg (生产及 pgvector 模式) 或 SQLite+aiosqlite (本地轻量模式)
    DATABASE_URL: str = "sqlite+aiosqlite:///./omnidigest.db"

    # 缓存配置
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_CACHE_TTL: int = 86400  # 默认 24 小时

    # 文本处理阈值
    TOKEN_SPLIT_THRESHOLD: int = 4000

    # 爬虫反爬配置 (如知乎 Cookie)
    ZHIHU_COOKIE: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
