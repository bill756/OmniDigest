import json
import hashlib
import os
import sqlite3
import time
import asyncio
from typing import Optional, Dict, Any
from app.config import get_settings

settings = get_settings()

CACHE_DIR = os.path.join(os.getcwd(), "data")
SQLITE_CACHE_FILE = os.path.join(CACHE_DIR, "cache.db")


class CacheClient:
    """具备自动降级容灾与本地持久化机制的缓存客户端 (首选 Redis，降级 SQLite，双保内存)"""

    def __init__(self, redis_url: str, db_path: str = SQLITE_CACHE_FILE):
        self.redis_url = redis_url
        self.db_path = db_path
        self._redis = None
        self._memory_cache: Dict[str, str] = {}
        self._is_redis_available = False
        self._init_sqlite()

    def _init_sqlite(self):
        """初始化 SQLite 本地持久化缓存表结构"""
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS kv_cache (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        expire_at REAL NOT NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_expire ON kv_cache(expire_at)")
                conn.commit()
        except Exception:
            pass

    async def init(self):
        """尝试连接 Redis；若连接失败则保持 SQLite 持久化降级模式"""
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

    def _sqlite_get(self, key: str) -> Optional[str]:
        now = time.time()
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value, expire_at FROM kv_cache WHERE key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    val, exp = row
                    if exp > now:
                        return val
                    # 已过期清理
                    conn.execute("DELETE FROM kv_cache WHERE key = ?", (key,))
                    conn.commit()
        except Exception:
            pass
        return None

    def _sqlite_set(self, key: str, value_str: str, ttl: int):
        exp = time.time() + ttl
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_cache (key, value, expire_at) VALUES (?, ?, ?)",
                    (key, value_str, exp)
                )
                conn.commit()
        except Exception:
            pass

    async def get(self, key: str) -> Optional[Dict[str, Any]]:
        """获取缓存结果（Redis ➔ SQLite 本地持久化 ➔ 内存字典）"""
        try:
            if self._is_redis_available and self._redis:
                val = await self._redis.get(key)
                if val:
                    return json.loads(val)
        except Exception:
            pass

        # 2. SQLite 本地持久化缓存拦截
        val_str = await asyncio.to_thread(self._sqlite_get, key)
        if val_str:
            try:
                data = json.loads(val_str)
                self._memory_cache[key] = val_str
                return data
            except Exception:
                pass

        # 3. 内存字典兜底
        val = self._memory_cache.get(key)
        if val:
            try:
                return json.loads(val)
            except Exception:
                return None
        return None

    async def set(self, key: str, value: Dict[str, Any], ttl: int = 86400) -> None:
        """设置缓存并附带 TTL（Redis ➔ SQLite 本地持久化 ➔ 内存字典）"""
        dumped = json.dumps(value, ensure_ascii=False)
        try:
            if self._is_redis_available and self._redis:
                await self._redis.set(key, dumped, ex=ttl)
                return
        except Exception:
            pass

        # 写入 SQLite 本地持久化
        await asyncio.to_thread(self._sqlite_set, key, dumped, ttl)
        self._memory_cache[key] = dumped

    async def close(self):
        if self._redis:
            await self._redis.close()


cache_client = CacheClient(settings.REDIS_URL)


async def get_cache() -> CacheClient:
    return cache_client
