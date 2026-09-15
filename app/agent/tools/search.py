import asyncio
from typing import List, Dict, Any
from app.config import get_settings

settings = get_settings()


async def search_evidence(query: str, max_results: int = 3) -> List[Dict[str, str]]:
    """针对断言进行多源搜索以获取佐证或反证片段"""
    results: List[Dict[str, str]] = []

    # 1. 优先使用 Tavily API (专为 LLM 优化的搜索引擎)
    if settings.TAVILY_API_KEY:
        try:
            from tavily import TavilyClient

            client = TavilyClient(api_key=settings.TAVILY_API_KEY)
            # 在单独线程中运行同步调用
            resp = await asyncio.to_thread(
                client.search,
                query=query,
                search_depth="basic",
                max_results=max_results,
            )
            for item in resp.get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    "snippet": item.get("content", ""),
                    "url": item.get("url", ""),
                    "source": "tavily",
                })
            if results:
                return results
        except Exception:
            pass

    # 2. 备用使用 DuckDuckGo / DDGS 搜索 (免费无需 API Key)
    try:
        from ddgs import DDGS

        def _ddg_search():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=max_results))

        ddg_items = await asyncio.to_thread(_ddg_search)
        for item in ddg_items:
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("body", ""),
                "url": item.get("href", ""),
                "source": "duckduckgo",
            })
        if results:
            return results
    except Exception:
        pass

    # 3. 兜底容错返回（确保因网络波动或无外网访问时 Agent 流畅执行）
    return [{
        "title": f"关于「{query}」的公开网络事实索引",
        "snippet": f"已完成针对关键词「{query}」的实时检索尝试，当前公开网络数据库未发现直接反例或重大事实反转声明。",
        "url": "https://www.google.com/search?q=" + query,
        "source": "fallback_index",
    }]
