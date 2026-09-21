import re
from copy import deepcopy
from typing import Optional, Dict
from lxml.html import tostring
from markdownify import markdownify
from scrapling import AsyncFetcher, Selector
from scrapling.engines.toolbelt.custom import Response
from app.config import get_settings
from app.crawler.base import BaseCrawler, ArticleContent


class ZhihuCrawler(BaseCrawler):
    """基于 Scrapling 的知乎专栏/问答 (zhihu.com) 特化抓取与正文清洗器"""

    # 知乎 WAF 盾要求真实 Chrome 完整的 Client Hints 与导航请求头
    ZHIHU_CHROME_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.zhihu.com/",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "Cache-Control": "max-age=0",
    }

    def __init__(self, cookie: Optional[str] = None, timeout: float = 10.0):
        super().__init__(timeout=timeout)
        self.cookie = cookie

    def _get_cookie(self) -> Optional[str]:
        if self.cookie:
            return self.cookie
        try:
            return get_settings().ZHIHU_COOKIE
        except Exception:
            return None

    async def fetch_response(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
    ) -> Response:
        """异步抓取知乎网页，通过 Chrome 124 TLS 指纹及完整 Client Hints 绕过知乎反爬"""
        req_headers = dict(self.ZHIHU_CHROME_HEADERS)
        if headers:
            req_headers.update(headers)

        cookie_val = self._get_cookie()
        if cookie_val:
            req_headers["Cookie"] = cookie_val.strip()

        try:
            resp = await AsyncFetcher.get(
                url,
                headers=req_headers,
                cookies=cookies,
                timeout=int(self.timeout),
                follow_redirects=True,
                impersonate="chrome124",
                stealthy_headers=False,  # 保持确定性 Chrome 124 头，避免被随机生成的 UA 破坏 Cookie 绑定
                retries=2,
            )

            # 1. 检测是否被知乎 302 重定向拦截到非人验证码挑战页面
            resp_url_str = str(resp.url).lower()
            if "account/unhuman" in resp_url_str or "unhuman" in resp_url_str:
                raise RuntimeError(
                    "知乎触发了平台安全验证挑战（请求被重定向至 /account/unhuman 验证码拦截页）。"
                    "原因分析：当前客户端缺少有效 Cookie、Cookie 绑定的设备指纹失效，或触发了频率风控。"
                    "解决建议：在 .env 中填入最新登录的 ZHIHU_COOKIE，或直接在页面下方使用「直接粘贴正文」模式。"
                )

            # 2. 检查 403 状态码并诊断原因
            if resp.status == 403:
                if not cookie_val:
                    raise RuntimeError(
                        "知乎返回 403 Forbidden（触发平台 JS 安全盾拦截）。"
                        "请在 .env 中配置有效 ZHIHU_COOKIE，或使用页面下方的「直接粘贴正文」模式。"
                    )
                else:
                    raise RuntimeError(
                        "知乎返回 403 Forbidden（当前配置的 ZHIHU_COOKIE 可能已过期失效或未绑定当前客户端环境）。"
                        "请在 .env 中更新 ZHIHU_COOKIE，或使用页面下方的「直接粘贴正文」模式。"
                    )
            elif resp.status == 404:
                raise RuntimeError("知乎页面不存在或已被作者删除 (HTTP 404)。请检查文章链接是否正确。")
            elif resp.status >= 400:
                raise RuntimeError(f"知乎页面请求失败 (HTTP {resp.status})。")

            return resp
        except Exception as e:
            err_str = str(e)
            if isinstance(e, RuntimeError):
                raise
            if "timed out" in err_str.lower() or "timeout" in err_str.lower():
                raise RuntimeError("请求知乎网页超时（超过 10 秒无响应）。")
            raise RuntimeError(f"知乎网络抓取异常 ({err_str})")

    def _convert_node_to_markdown(self, container_el) -> str:
        """清洗正文节点并转换为纯净 Markdown（保留 LaTeX 公式并剔除交互按钮和广告）"""
        if container_el is None:
            return ""

        root = deepcopy(container_el._root if hasattr(container_el, "_root") else container_el)

        # 1. 剔除噪声标签
        for noise_tag in ("script", "style", "noscript", "svg", "button"):
            for el in list(root.iter(noise_tag)):
                el.drop_tree()

        # 2. 剔除知乎特定交互组件
        for bad in list(root.cssselect(".ContentItem-time, .Reward, .ContentItem-actions, .Sticky")):
            bad.drop_tree()

        # 3. 处理数学公式：提取 data-ee 属性为标准 Markdown 行内公式
        for math_el in list(root.cssselect(".ztext-math")):
            tex = math_el.get("data-ee")
            if tex:
                math_el.text = f" ${tex}$ "

        # 4. 转换 Markdown 并清理多余空行
        html_str = tostring(root, encoding="unicode")
        md = markdownify(html_str, heading_style="ATX", strip=['script', 'style', 'noscript'])
        return re.sub(r"\n{3,}", "\n\n", md).strip()

    async def extract(self, url: str, html: Optional[str] = None) -> ArticleContent:
        resp: Optional[Response] = None
        if not html:
            resp = await self.fetch_response(url)
            html = resp.html_content or (
                resp.body.decode("utf-8", errors="ignore")
                if isinstance(resp.body, bytes)
                else str(resp.body)
            )
            sel = resp
        else:
            sel = Selector(content=html, url=url)

        if "zh-zse-ck" in html:
            cookie_val = self._get_cookie()
            if not cookie_val:
                raise RuntimeError("知乎页面启用了动态 JS 安全盾 (zse-ck 验证挑战)。请在 .env 中配置有效 ZHIHU_COOKIE，或使用下方的「直接粘贴正文」模式。")
            else:
                raise RuntimeError("知乎页面启用了动态 JS 安全盾 (zse-ck 验证挑战)，当前 ZHIHU_COOKIE 可能已失效。请更新 Cookie 或使用下方的「直接粘贴正文」模式。")

        if "没有知识存在的荒原" in html or "404 - 知乎" in html:
            raise RuntimeError("知乎页面显示「没有知识存在的荒原」(HTTP 404)，该文章可能不存在、已被作者删除或链接有误。")

        # 1. 提取标题 (专栏 .Post-Title 或 问答 .QuestionHeader-title)
        title = (
            sel.css("h1.Post-Title::text").get()
            or sel.css("h1.QuestionHeader-title::text").get()
            or sel.css("h1::text").get()
            or sel.css("title::text").get()
            or "知乎内容"
        ).strip()
        title = re.sub(r"\s*-\s*知乎.*$", "", title)

        # 2. 提取作者
        author = (
            sel.css("div.AuthorInfo-name::text").get()
            or sel.css("span.UserLink::text").get()
            or sel.css("meta[property='og:author']::attr(content)").get()
            or sel.css("meta[itemprop='name']::attr(content)").get()
        )
        if author:
            author = author.strip()

        # 3. 提取正文内容 (支持单篇专栏文章与问答多回答)
        cleaned_content = ""
        question_header = sel.css("h1.QuestionHeader-title")
        list_items = sel.css("div.List-item") if question_header else []

        if list_items:
            # 问答页面包含回答列表
            answers_text = []
            for idx, item in enumerate(list_items, 1):
                ans_author = (
                    item.css("div.AuthorInfo-name::text").get()
                    or item.css("span.UserLink::text").get()
                    or item.css("meta[itemprop='name']::attr(content)").get()
                    or "匿名用户"
                ).strip()

                ans_content_nodes = item.css("div.RichContent-inner")
                if ans_content_nodes:
                    ans_md = self._convert_node_to_markdown(ans_content_nodes[0])
                    if ans_md:
                        answers_text.append(f"### 回答 {idx} (答主: {ans_author})\n\n{ans_md}")

            if answers_text:
                cleaned_content = "\n\n---\n\n".join(answers_text)

        if not cleaned_content:
            # 专栏文章或单回答页面提取
            content_nodes = sel.css(
                "div.Post-RichTextContainer, div.RichContent-inner, div.RichText"
            )
            if not content_nodes:
                # 回退到基类通用抽取
                return await super().extract(url, html)

            cleaned_content = self._convert_node_to_markdown(content_nodes[0])

        if len(cleaned_content) < 50:
            fallback = await super().extract(url, html)
            if len(fallback.content) > len(cleaned_content):
                cleaned_content = fallback.content

        return ArticleContent(
            url=url,
            title=title,
            content=cleaned_content,
            author=author,
            publish_date=None,
            platform="zhihu",
            raw_html=html,
        )
