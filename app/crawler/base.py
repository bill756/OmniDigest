import re
from dataclasses import dataclass, field
from typing import Optional, Dict
import httpx
import trafilatura
from bs4 import BeautifulSoup


@dataclass
class ArticleContent:
    url: str
    title: str
    content: str  # 清洗后的纯净 Markdown / 结构化正文
    author: Optional[str] = None
    publish_date: Optional[str] = None
    platform: str = "web"
    raw_html: Optional[str] = None
    metadata: Dict = field(default_factory=dict)


class BaseCrawler:
    """通用网页抓取与基于 trafilatura 的智能降噪清洗器"""

    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
    }

    def __init__(self, timeout: float = 10.0):
        self.timeout = httpx.Timeout(timeout, connect=5.0)

    async def fetch_html(self, url: str) -> str:
        """异步抓取原始网页 HTML，并对网络风控提供清晰反馈"""
        try:
            async with httpx.AsyncClient(
                headers=self.DEFAULT_HEADERS,
                follow_redirects=True,
                timeout=self.timeout,
                verify=False,
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 403:
                    raise RuntimeError(f"目标网站返回 403 Forbidden（触发平台安全验证与反爬拦截），无法直接抓取。建议使用页面下方的「直接粘贴正文」模式。")
                elif resp.status_code == 404:
                    raise RuntimeError(f"目标网页不存在或已被作者删除 (HTTP 404)。请检查文章链接是否正确。")
                resp.raise_for_status()
                return resp.text
        except httpx.TimeoutException:
            raise RuntimeError(f"请求网页超时（超过 10 秒无响应），目标服务器连接受阻。")
        except httpx.HTTPError as e:
            raise RuntimeError(f"网络抓取异常 ({str(e)})")

    async def extract(self, url: str, html: Optional[str] = None) -> ArticleContent:
        """从 URL 或传入的 HTML 中提取标题与纯净正文"""
        if not html:
            html = await self.fetch_html(url)

        # 优先使用 trafilatura 高精度正文抽取
        extracted_text = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_links=True,
            include_images=False,
            include_tables=True,
            favor_precision=True,
        )

        metadata = trafilatura.extract_metadata(html, default_url=url)
        title = (metadata.title if metadata and metadata.title else None)
        author = (metadata.author if metadata and metadata.author else None)
        date = (metadata.date if metadata and metadata.date else None)

        if not extracted_text:
            # 回退到 BeautifulSoup 备用提取
            soup = BeautifulSoup(html, "html.parser")
            # 剔除无用标签
            for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
                tag.decompose()
            title = title or (soup.title.string.strip() if soup.title else "未命名文章")
            extracted_text = soup.get_text(separator="\n\n", strip=True)

        if not title:
            soup = BeautifulSoup(html, "html.parser")
            title = soup.title.string.strip() if soup.title else "未命名文章"

        return ArticleContent(
            url=url,
            title=title or "未命名文章",
            content=extracted_text or "",
            author=author,
            publish_date=date,
            platform="web",
            raw_html=html,
        )
