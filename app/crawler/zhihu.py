import re
from typing import Optional
import httpx
from bs4 import BeautifulSoup
from app.config import get_settings
from app.crawler.base import BaseCrawler, ArticleContent


class ZhihuCrawler(BaseCrawler):
    """知乎专栏/问答 (zhihu.com) 特化抓取与正文清洗器"""

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

    async def fetch_html(self, url: str) -> str:
        """异步抓取知乎网页 HTML，自动注入防爬 Cookie 并做针对性错误诊断"""
        headers = dict(self.DEFAULT_HEADERS)
        headers["Referer"] = "https://www.zhihu.com/"
        cookie_val = self._get_cookie()
        if cookie_val:
            headers["Cookie"] = cookie_val.strip()

        try:
            async with httpx.AsyncClient(
                headers=headers,
                follow_redirects=True,
                timeout=self.timeout,
                verify=False,
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 403:
                    if not cookie_val:
                        raise RuntimeError(
                            "知乎返回 403 Forbidden（触发平台 JS 安全盾拦截）。"
                            "请在 .env 中配置有效 ZHIHU_COOKIE，或使用页面下方的「直接粘贴正文」模式。"
                        )
                    else:
                        raise RuntimeError(
                            "知乎返回 403 Forbidden（当前配置的 ZHIHU_COOKIE 可能已过期失效）。"
                            "请在 .env 中更新 ZHIHU_COOKIE，或使用页面下方的「直接粘贴正文」模式。"
                        )
                elif resp.status_code == 404:
                    raise RuntimeError("知乎页面不存在或已被作者删除 (HTTP 404)。请检查文章链接是否正确。")
                resp.raise_for_status()
                return resp.text
        except httpx.TimeoutException:
            raise RuntimeError("请求知乎网页超时（超过 10 秒无响应）。")
        except httpx.HTTPError as e:
            raise RuntimeError(f"知乎网络抓取异常 ({str(e)})")

    def _convert_node_to_markdown(self, container_el) -> str:
        """清洗正文节点并转换为纯净 Markdown"""
        if not container_el:
            return ""

        # 剔除噪声组件
        for noise in container_el.select("script, style, noscript, svg, button, .ContentItem-time, .Reward"):
            noise.decompose()

        # 保留公式
        for math_tag in container_el.select(".ztext-math"):
            tex = math_tag.get("data-ee")
            if tex:
                math_tag.replace_with(f" ${tex}$ ")

        markdown_lines = []
        for child in container_el.children:
            if not hasattr(child, "name") or not child.name:
                text = str(child).strip()
                if text:
                    markdown_lines.append(text)
                continue

            name = child.name.lower()
            text = child.get_text(separator=" ", strip=True)
            if not text:
                continue

            if name in ["h1", "h2"]:
                markdown_lines.append(f"\n## {text}\n")
            elif name in ["h3", "h4"]:
                markdown_lines.append(f"\n### {text}\n")
            elif name in ["blockquote", "q"]:
                markdown_lines.append(f"> {text}\n")
            elif name in ["ul", "ol"]:
                for li in child.find_all("li"):
                    li_text = li.get_text(strip=True)
                    if li_text:
                        markdown_lines.append(f"- {li_text}")
                markdown_lines.append("")
            elif name == "p":
                markdown_lines.append(f"{text}\n")
            else:
                markdown_lines.append(f"{text}\n")

        cleaned_content = "\n".join(markdown_lines)
        return re.sub(r"\n{3,}", "\n\n", cleaned_content).strip()

    async def extract(self, url: str, html: Optional[str] = None) -> ArticleContent:
        if not html:
            html = await self.fetch_html(url)

        if "zh-zse-ck" in html:
            cookie_val = self._get_cookie()
            if not cookie_val:
                raise RuntimeError("知乎页面启用了动态 JS 安全盾 (zse-ck 验证挑战)。请在 .env 中配置有效 ZHIHU_COOKIE，或使用下方的「直接粘贴正文」模式。")
            else:
                raise RuntimeError("知乎页面启用了动态 JS 安全盾 (zse-ck 验证挑战)，当前 ZHIHU_COOKIE 可能已失效。请更新 Cookie 或使用下方的「直接粘贴正文」模式。")

        if "没有知识存在的荒原" in html or "404 - 知乎" in html:
            raise RuntimeError("知乎页面显示「没有知识存在的荒原」(HTTP 404)，该文章可能不存在、已被作者删除或链接有误。")

        soup = BeautifulSoup(html, "html.parser")

        # 1. 提取标题 (专栏 .Post-Title 或 问答 .QuestionHeader-title)
        title_el = (
            soup.find("h1", class_="Post-Title")
            or soup.find("h1", class_="QuestionHeader-title")
            or soup.find("h1")
        )
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            title = soup.title.get_text(strip=True) if soup.title else "知乎内容"
            title = re.sub(r"\s*-\s*知乎.*$", "", title)

        # 2. 提取作者
        author = None
        author_el = (
            soup.find("span", class_="UserLink")
            or soup.find("div", class_="AuthorInfo-name")
            or soup.find("meta", attrs={"property": "og:author"})
            or soup.find("meta", attrs={"itemprop": "name"})
        )
        if author_el:
            if author_el.name == "meta":
                author = author_el.get("content")
            else:
                author = author_el.get_text(strip=True)

        # 3. 提取正文内容 (支持单篇专栏文章与问答多回答)
        cleaned_content = ""
        question_header = soup.find("h1", class_="QuestionHeader-title")
        list_items = soup.find_all("div", class_="List-item") if question_header else []

        if list_items:
            # 问答页面有明确回答列表
            answers_text = []
            for idx, item in enumerate(list_items, 1):
                ans_author_el = (
                    item.find("div", class_="AuthorInfo-name")
                    or item.find("span", class_="UserLink")
                    or item.find("meta", attrs={"itemprop": "name"})
                )
                ans_author = "匿名用户"
                if ans_author_el:
                    ans_author = (
                        ans_author_el.get("content")
                        if ans_author_el.name == "meta"
                        else ans_author_el.get_text(strip=True)
                    ) or "匿名用户"

                ans_content_el = item.find("div", class_="RichContent-inner")
                if ans_content_el:
                    ans_md = self._convert_node_to_markdown(ans_content_el)
                    if ans_md:
                        answers_text.append(f"### 回答 {idx} (答主: {ans_author})\n\n{ans_md}")

            if answers_text:
                cleaned_content = "\n\n---\n\n".join(answers_text)

        if not cleaned_content:
            # 专栏文章或单回答页面提取
            content_el = (
                soup.find("div", class_="Post-RichTextContainer")
                or soup.find("div", class_="RichContent-inner")
                or soup.find("div", class_="RichText")
            )

            if not content_el:
                # 回退到基类通用抽取
                return await super().extract(url, html)

            cleaned_content = self._convert_node_to_markdown(content_el)

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

