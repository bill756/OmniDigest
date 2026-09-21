import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Union
import trafilatura
from lxml.html import tostring, HtmlElement
from markdownify import markdownify
from scrapling import AsyncFetcher, Selector
from scrapling.engines.toolbelt.custom import Response


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


def clean_node_to_markdown(
    node: Union[Selector, HtmlElement],
    noise_selectors: Optional[List[str]] = None,
    drop_tags: tuple = ("script", "style", "noscript", "svg", "button", "iframe", "nav", "footer"),
) -> str:
    """使用 lxml 树与 markdownify 将 DOM 节点转换为规范纯净的 Markdown 文本，并剔除噪声"""
    if node is None:
        return ""

    if hasattr(node, "_root"):
        root = deepcopy(node._root)
    elif isinstance(node, HtmlElement):
        root = deepcopy(node)
    else:
        return ""

    # 1. 剔除指定 tag
    for tag in drop_tags:
        for el in list(root.iter(tag)):
            el.drop_tree()

    # 2. 剔除 CSS 噪声选择器
    if noise_selectors:
        for sel in noise_selectors:
            for bad in list(root.cssselect(sel)):
                bad.drop_tree()

    html_str = tostring(root, encoding="unicode")
    md = markdownify(html_str, heading_style="ATX", strip=['script', 'style', 'noscript'])
    # 规整化连续空行
    return re.sub(r"\n{3,}", "\n\n", md).strip()


class BaseCrawler:
    """基于 Scrapling (curl_cffi TLS 指纹伪装与高精度解析器) 的通用网页抓取与降噪清洗器"""

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
        self.timeout = timeout

    async def fetch_response(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
    ) -> Response:
        """异步抓取网页，利用 Scrapling 原生 TLS 指纹伪装模拟最新 Chrome，绕过常见反爬"""
        req_headers = dict(self.DEFAULT_HEADERS)
        if headers:
            req_headers.update(headers)

        try:
            resp = await AsyncFetcher.get(
                url,
                headers=req_headers,
                cookies=cookies,
                timeout=int(self.timeout),
                follow_redirects=True,
                impersonate="chrome",
                stealthy_headers=True,
                retries=2,
            )
            if resp.status == 403:
                raise RuntimeError("目标网站返回 403 Forbidden（触发平台安全验证与反爬拦截），无法直接抓取。建议使用页面下方的「直接粘贴正文」模式。")
            elif resp.status == 404:
                raise RuntimeError("目标网页不存在或已被作者删除 (HTTP 404)。请检查文章链接是否正确。")
            elif resp.status >= 400:
                raise RuntimeError(f"目标网页请求失败 (HTTP {resp.status})。请稍后重试或检查链接。")

            return resp
        except Exception as e:
            err_str = str(e)
            if isinstance(e, RuntimeError):
                raise
            if "timed out" in err_str.lower() or "timeout" in err_str.lower():
                raise RuntimeError(f"请求网页超时（超过 {int(self.timeout)} 秒无响应），目标服务器连接受阻。")
            raise RuntimeError(f"网络抓取异常 ({err_str})")

    async def fetch_html(self, url: str) -> str:
        """异步抓取原始网页 HTML"""
        resp = await self.fetch_response(url)
        return resp.text or resp.html_content

    async def extract(self, url: str, html: Optional[str] = None) -> ArticleContent:
        """从 URL 或传入的 HTML 中提取标题、元数据与纯净正文"""
        resp: Optional[Response] = None
        if not html:
            resp = await self.fetch_response(url)
            html = resp.text or resp.html_content
            sel = resp
        else:
            sel = Selector(content=html, url=url)

        # 1. 优先使用 Scrapling Selector 提取标题
        title = (
            sel.css("meta[property='og:title']::attr(content)").get()
            or sel.css("meta[name='twitter:title']::attr(content)").get()
            or sel.css("title::text").get()
            or sel.css("h1::text").get()
        )
        if title:
            title = title.strip()

        # 2. 提取作者与发布时间
        author = (
            sel.css("meta[name='author']::attr(content)").get()
            or sel.css("meta[property='article:author']::attr(content)").get()
        )
        if author:
            author = author.strip()

        publish_date = (
            sel.css("meta[property='article:published_time']::attr(content)").get()
            or sel.css("meta[name='pubdate']::attr(content)").get()
            or sel.css("meta[name='publishdate']::attr(content)").get()
        )
        if publish_date:
            publish_date = publish_date.strip()

        # 3. 正文抽取：优先使用 trafilatura 抽取长文
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
        if metadata:
            title = title or (metadata.title.strip() if metadata.title else None)
            author = author or (metadata.author.strip() if metadata.author else None)
            publish_date = publish_date or (metadata.date.strip() if metadata.date else None)

        # 4. 若 trafilatura 抽取为空或过短，使用 Scrapling Selector 进行智能备用提取
        if not extracted_text or len(extracted_text.strip()) < 50:
            main_nodes = sel.css("article, main, #content, .post-content, .entry-content, body")
            target_node = main_nodes[0] if main_nodes else sel
            extracted_text = clean_node_to_markdown(
                target_node,
                noise_selectors=[".ad", ".advertisement", ".banner", ".popup", "header", "footer", "nav", "aside"],
            )

        if not title:
            title_node = sel.css("title::text")
            title = title_node.get().strip() if title_node else "未命名文章"

        return ArticleContent(
            url=url,
            title=title or "未命名文章",
            content=extracted_text or "",
            author=author,
            publish_date=publish_date,
            platform="web",
            raw_html=html,
        )
