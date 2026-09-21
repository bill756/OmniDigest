import re
from typing import Optional
from scrapling import Selector
from scrapling.engines.toolbelt.custom import Response
from app.crawler.base import BaseCrawler, ArticleContent, clean_node_to_markdown


class WechatCrawler(BaseCrawler):
    """基于 Scrapling 的微信公众号 (mp.weixin.qq.com) 特化抓取与纯净正文提取器"""

    async def extract(self, url: str, html: Optional[str] = None) -> ArticleContent:
        resp: Optional[Response] = None
        if not html:
            resp = await self.fetch_response(url)
            html = resp.text or resp.html_content
            sel = resp
        else:
            sel = Selector(content=html, url=url)

        # 1. 提取标题
        title = (
            sel.css("#activity-name::text").get()
            or sel.css("h1.rich_media_title::text").get()
            or sel.css("title::text").get()
            or "微信公众号文章"
        ).strip()

        # 2. 提取作者/公众号名
        author = (
            sel.css("#js_name::text").get()
            or sel.css("#js_author_name::text").get()
            or sel.css("strong.profile_nickname::text").get()
            or sel.css(".profile_nickname::text").get()
        )
        if author:
            author = author.strip()

        # 3. 提取发布时间
        publish_date = (
            sel.css("#publish_time::text").get()
            or sel.css("em#publish_time::text").get()
        )
        if publish_date:
            publish_date = publish_date.strip()

        # 4. 定位正文容器 #js_content
        content_nodes = sel.css("#js_content")
        if not content_nodes:
            # 回退到基类通用抽取
            return await super().extract(url, html)

        content_node = content_nodes[0]

        # 5. 清洗噪声并生成纯净 Markdown
        cleaned_content = clean_node_to_markdown(
            content_node,
            noise_selectors=[
                ".qr_code_pc",
                "#js_toobar3",
                ".weui-dialog",
                ".reward_area",
                ".rich_media_tool",
            ],
            drop_tags=("script", "style", "iframe", "noscript"),
        )

        # 6. 若提取长度过短，再次以基类抽取互补
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
