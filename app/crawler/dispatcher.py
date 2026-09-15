from urllib.parse import urlparse
from typing import Optional
from app.crawler.base import BaseCrawler, ArticleContent
from app.crawler.wechat import WechatCrawler
from app.crawler.zhihu import ZhihuCrawler


class CrawlerDispatcher:
    """根据 URL 自动路由匹配最适合的清洗爬虫"""

    def __init__(self):
        self.wechat_crawler = WechatCrawler()
        self.zhihu_crawler = ZhihuCrawler()
        self.base_crawler = BaseCrawler()

    def select_crawler(self, url: str) -> BaseCrawler:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()

        if "mp.weixin.qq.com" in netloc:
            return self.wechat_crawler
        elif "zhihu.com" in netloc:
            return self.zhihu_crawler
        else:
            return self.base_crawler

    async def crawl(self, url: str, html: Optional[str] = None) -> ArticleContent:
        crawler = self.select_crawler(url)
        return await crawler.extract(url, html=html)


crawler_dispatcher = CrawlerDispatcher()
