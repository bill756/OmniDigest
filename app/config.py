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
    # 向量嵌入配置 (支持外部兼容 OpenAI 规范接口，若未配置或为 DeepSeek 则自动启用本地高性能向量引擎)
    EMBEDDING_API_KEY: Optional[str] = None
    EMBEDDING_BASE_URL: Optional[str] = None
    EMBEDDING_MODEL_NAME: str = "BAAI/bge-small-zh-v1.5"
    EMBEDDING_THRESHOLD: float = 0.30  # 相似度召回门槛，过滤无关噪音

    @property
    def is_external_embedding_available(self) -> bool:
        """检查是否有可用且非 DeepSeek 的外部 Embedding 服务"""
        base_url = (self.EMBEDDING_BASE_URL or self.OPENAI_BASE_URL or "").lower()
        api_key = self.EMBEDDING_API_KEY or self.OPENAI_API_KEY
        if not api_key:
            return False
        if "deepseek.com" in base_url:
            return False
        return bool(base_url)

    # 联网搜索核查配置 (Tavily 或 DuckDuckGo)
    TAVILY_API_KEY: Optional[str] = None

    # TypeSafe AI (Jev System One) 配置
    TYPESAFE_API_KEY: Optional[str] = None
    TYPESAFE_ENDPOINT: str = "https://api.typesafe.ai"
    TYPESAFE_MODEL: str = "jev-latest"

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


def update_zhihu_cookie(new_cookie: str) -> None:
    """动态热更新知乎 Cookie，无需重启服务"""
    settings = get_settings()
    settings.ZHIHU_COOKIE = new_cookie
