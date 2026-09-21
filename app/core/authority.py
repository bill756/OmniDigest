import re
from urllib.parse import urlparse
from typing import Dict, Any, Tuple, List, Set

# Tier 1: 官方监管机构、政府白皮书、国际组织、顶级学术期刊与论文库 (权威系数 1.0)
TIER_1_ACADEMIC_DOMAINS: Set[str] = {
    "nature.com",
    "science.org",
    "cell.com",
    "ieee.org",
    "acm.org",
    "arxiv.org",
    "ncbi.nlm.nih.gov",
    "pubmed.ncbi.nlm.nih.gov",
    "thelancet.com",
    "nejm.org",
    "sciencedirect.com",
    "springer.com",
    "wiley.com",
    "pnas.org",
    "iop.org",
    "aps.org",
    "rsc.org",
    "acs.org",
}

TIER_1_GOV_DOMAINS: Set[str] = {
    "gov.cn",
    "gov",
    "who.int",
    "fda.gov",
    "sec.gov",
    "wipo.int",
    "iso.org",
    "itu.int",
    "un.org",
    "imf.org",
    "worldbank.org",
    "stats.gov.cn",
    "miit.gov.cn",
    "most.gov.cn",
    "csrc.gov.cn",
    "nmpa.gov.cn",
}

# Tier 2: 知名深度财经媒体、主流新闻通讯社、专业科技评测机构 (权威系数 0.8)
TIER_2_NEWS_DOMAINS: Set[str] = {
    "reuters.com",
    "bloomberg.com",
    "caixin.com",
    "xinhuanet.com",
    "people.com.cn",
    "wsj.com",
    "ft.com",
    "thepaper.cn",
    "yicai.com",
    "techcrunch.com",
    "theverge.com",
    "wired.com",
    "technologyreview.com",
    "scmp.com",
    "bbc.com",
    "nytimes.com",
    "economist.com",
    "36kr.com",
    "huxiu.com",
    "caijing.com.cn",
    "jiemian.com",
    "cctv.com",
    "news.cn",
    "chinanews.com.cn",
    "chinanews.com",
    "bjnews.com.cn",
    "oeeee.com",
    "nddaily.com",
    "hk01.com",
    "china.com",
    "qq.com",
    "163.com",
    "ifeng.com",
    "huanqiu.com",
    "guancha.cn",
    "stcn.com",
    "cls.cn",
    "sohu.com",
}

# 百科与基准常模信源
TIER_BENCHMARK_DOMAINS: Set[str] = {
    "wikipedia.org",
    "baike.baidu.com",
    "wikidata.org",
}

# 垃圾内容农场、SEO 洗稿站、UGC 问答灌水黑名单（全面屏蔽与剔除）
LOW_QUALITY_DOMAINS: List[str] = [
    "baijiahao.baidu.com",
    "360doc.com",
    "blog.csdn.net",
    "csdn.net",
    "zhidao.baidu.com",
    "bilibili.com/read",
    "kuaibao.qq.com",
    "163.com/dy",
    "weibo.com",
    "xiaohongshu.com",
    "douyin.com",
    "kuaishou.com",
    "zhihu.com/question",
    "docin.com",
    "wenku.baidu.com",
]


def extract_root_domain(url: str) -> str:
    """从 URL 中精准提取主域名/根域名（如 https://api.nature.com/path -> nature.com）"""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower().split(":")[0]
        if not netloc:
            return ""

        parts = netloc.split(".")
        if len(parts) >= 2:
            # 兼容两级国家代码后缀，如 .gov.cn, .com.cn, .edu.cn, .org.cn
            if len(parts) >= 3 and parts[-2] in ("gov", "com", "edu", "org", "net") and parts[-1] in ("cn", "uk", "jp", "hk"):
                return ".".join(parts[-3:])
            return ".".join(parts[-2:])
        return netloc
    except Exception:
        return ""


def classify_source_tier(url: str) -> Dict[str, Any]:
    """计算信源的权威等级、权威权重与中文展示标签"""
    root_domain = extract_root_domain(url)

    # 检查是否为政府/顶级学术期刊一手信源 (Tier 1)
    if (
        root_domain in TIER_1_ACADEMIC_DOMAINS
        or root_domain in TIER_1_GOV_DOMAINS
        or root_domain.endswith(".gov.cn")
        or root_domain.endswith(".gov")
    ):
        return {
            "tier": "tier_1",
            "tier_label": "🏛️ Tier 1 (官方监管/顶刊一手)",
            "weight": 1.0,
            "root_domain": root_domain,
            "is_authoritative": True,
        }

    # 检查是否为主流新闻通讯社/专业深度媒体 (Tier 2)
    if root_domain in TIER_2_NEWS_DOMAINS:
        return {
            "tier": "tier_2",
            "tier_label": "📰 Tier 2 (主流通讯社/专业深度媒体)",
            "weight": 0.8,
            "root_domain": root_domain,
            "is_authoritative": True,
        }

    # 检查是否为基准百科常模 (Benchmark)
    if root_domain in TIER_BENCHMARK_DOMAINS:
        return {
            "tier": "tier_benchmark",
            "tier_label": "📚 行业常模/公开百科基准",
            "weight": 0.75,
            "root_domain": root_domain,
            "is_authoritative": True,
        }

    # 一般公开网络信源 (Tier 3)
    return {
        "tier": "tier_3",
        "tier_label": "🌐 Tier 3 (公开网络一般信源)",
        "weight": 0.45,
        "root_domain": root_domain,
        "is_authoritative": False,
    }


def is_blacklisted_domain(url: str) -> bool:
    """检测 URL 是否属于垃圾内容农场或 UGC 灌水黑名单"""
    if not url:
        return False
    lower_url = url.lower()
    for bad in LOW_QUALITY_DOMAINS:
        if bad in lower_url:
            return True
    return False
