import json
import hashlib
from typing import Optional, Dict, Any
from app.config import get_settings
from app.db.session import get_db

settings = get_settings()


class CacheClient:
    """具备自动降级容灾机制的缓存客户端 (Redis 或内存字典)"""

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self._redis = None
        self._memory_cache: Dict[str, str] = {}
        self._is_redis_available = False

    async def init(self):
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=2.0,
            )
            await self._redis.ping()
            self._is_redis_available = True
        except Exception:
            self._is_redis_available = False
            self._redis = None

    @staticmethod
    def get_url_hash(url: str) -> str:
        """生成标准化的 URL Hash"""
        clean_url = url.strip().split("#")[0]
        return hashlib.sha256(clean_url.encode("utf-8")).hexdigest()

    async def get(self, key: str) -> Optional[Dict[str, Any]]:
        """获取缓存结果"""
        try:
            if self._is_redis_available and self._redis:
                val = await self._redis.get(key)
                if val:
                    return json.loads(val)
        except Exception:
            pass

        # 内存缓存兜底
        val = self._memory_cache.get(key)
        if val:
            try:
                return json.loads(val)
            except Exception:
                return None
        return None

    async def set(self, key: str, value: Dict[str, Any], ttl: int = 86400) -> None:
        """设置缓存并附带 TTL"""
        dumped = json.dumps(value, ensure_ascii=False)
        try:
            if self._is_redis_available and self._redis:
                await self._redis.set(key, dumped, ex=ttl)
                return
        except Exception:
            pass

        self._memory_cache[key] = dumped

    async def close(self):
        if self._redis:
            await self._redis.close()


cache_client = CacheClient(settings.REDIS_URL)


async def get_cache() -> CacheClient:
    return cache_client
