import re
from typing import Optional
from bs4 import BeautifulSoup
from app.crawler.base import BaseCrawler, ArticleContent


class WechatCrawler(BaseCrawler):
    """微信公众号 (mp.weixin.qq.com) 特化抓取与纯净正文提取器"""

    async def extract(self, url: str, html: Optional[str] = None) -> ArticleContent:
        if not html:
            html = await self.fetch_html(url)

        soup = BeautifulSoup(html, "html.parser")

        # 1. 提取标题
        title_el = soup.find(id="activity-name") or soup.find("h1", class_="rich_media_title")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            title = soup.title.get_text(strip=True) if soup.title else "微信公众号文章"

        # 2. 提取作者/公众号名
        author = None
        author_el = soup.find(id="js_name") or soup.find(id="js_author_name") or soup.find("strong", class_="profile_nickname")
        if author_el:
            author = author_el.get_text(strip=True)

        # 3. 提取发布时间
        publish_date = None
        time_el = soup.find(id="publish_time") or soup.find("em", id="publish_time")
        if time_el:
            publish_date = time_el.get_text(strip=True)

        # 4. 定位正文容器 #js_content
        content_el = soup.find(id="js_content")
        if not content_el:
            # 回退到基类通用抽取
            return await super().extract(url, html)

        # 5. 剔除噪声标签 (JS、样式、二维码、推荐卡片、投票组件等)
        for noise in content_el.select("script, style, iframe, noscript, .qr_code_pc, #js_toobar3, .weui-dialog, .reward_area"):
            noise.decompose()

        # 6. 处理标题标签和段落格式，生成纯净 Markdown
        markdown_lines = []
        for elem in content_el.children:
            if not hasattr(elem, "name") or not elem.name:
                text = str(elem).strip()
                if text:
                    markdown_lines.append(text)
                continue

            name = elem.name.lower()
            text = elem.get_text(separator=" ", strip=True)
            if not text:
                continue

            if name in ["h1", "h2"]:
                markdown_lines.append(f"\n## {text}\n")
            elif name in ["h3", "h4"]:
                markdown_lines.append(f"\n### {text}\n")
            elif name in ["blockquote", "q"]:
                markdown_lines.append(f"> {text}\n")
            elif name in ["ul", "ol"]:
                for li in elem.find_all("li"):
                    li_text = li.get_text(strip=True)
                    if li_text:
                        markdown_lines.append(f"- {li_text}")
                markdown_lines.append("")
            else:
                # 常规段落 p, section, span, div
                markdown_lines.append(f"{text}\n")

        cleaned_content = "\n".join(markdown_lines)
        # 去除连续 3 个以上空行
        cleaned_content = re.sub(r"\n{3,}", "\n\n", cleaned_content).strip()

        # 若提取长度过短，再次以 trafilatura 互补
        if len(cleaned_content) < 50:
            fallback = await super().extract(url, html)
            if len(fallback.content) > len(cleaned_content):
                cleaned_content = fallback.content

        return ArticleContent(
            url=url,
            title=title,
            content=cleaned_content,
            author=author,
            publish_date=publish_date,
            platform="wechat",
            raw_html=html,
        )
