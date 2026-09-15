from app.crawler.base import BaseCrawler, ArticleContent
from app.crawler.wechat import WechatCrawler
from app.crawler.zhihu import ZhihuCrawler
from app.crawler.dispatcher import CrawlerDispatcher, crawler_dispatcher

__all__ = [
    "BaseCrawler",
    "ArticleContent",
    "WechatCrawler",
    "ZhihuCrawler",
    "CrawlerDispatcher",
    "crawler_dispatcher",
]
