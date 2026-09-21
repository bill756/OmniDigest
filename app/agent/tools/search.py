import asyncio
import hashlib
import re
from typing import List, Dict, Any, Optional
import httpx
from app.config import get_settings
from app.core.authority import (
    extract_root_domain,
    classify_source_tier,
    is_blacklisted_domain,
    LOW_QUALITY_DOMAINS,
)
from app.core.cache import cache_client

settings = get_settings()

STOP_PATTERNS = [
    r"一个?", r"一家", r"一位", r"一种", r"一些", r"这个?", r"这些", r"那个?", r"那些",
    r"使用", r"利用", r"采用", r"加工", r"进行", r"经过", r"由于", r"因为", r"所以",
    r"属于", r"关于", r"对于", r"根据", r"按照", r"通过", r"被", r"在", r"于",
    r"已", r"并", r"且", r"与", r"和", r"或", r"而", r"及", r"等", r"其",
    r"达到", r"显示", r"表明", r"指出", r"经检测", r"经", r"的的?", r"某",
]

COMMON_GEOGRAPHIC_ENTITIES = {
    "中国", "广东", "广东省", "潮州", "潮州市", "北京", "北京市", "上海", "上海市",
    "深圳", "广州", "浙江", "江苏", "四川", "山东", "河南", "湖北", "湖南",
    "美国", "英国", "日本", "德国", "全国", "全省", "全市",
}


SECONDARY_WORDS = {"检测", "测试", "数据", "报告", "情况", "方面", "肉脯", "食品", "制作", "原料", "相关", "最新", "全国"}


def extract_search_keywords(text: str) -> Dict[str, str]:
    """从断言长句或大模型生成的冗长 query 中智能提炼高信息密度的核心搜索实体与谓词组合，
    严格将查询词压缩在 2~3 个核心词内，杜绝多词导致的搜索引擎倒排索引失配与超时。
    """
    if not text:
        return {"core": "", "event": "", "refute": ""}

    text_stripped = text.strip()
    tokens = text_stripped.split()

    # 如果已经是空格切分的简短词组（2~5词且无句式标点），直接使用（如 AI 自主生成的精准词）
    has_sentence_punct = bool(re.search(r"[,，。；;！？!\n\t]", text_stripped))
    if not has_sentence_punct and 2 <= len(tokens) <= 5 and len(text_stripped) <= 45:
        return {
            "core": text_stripped,
            "event": text_stripped,
            "refute": f"{text_stripped} 辟谣",
        }

    # 标点符号分句分词
    clauses = re.split(r"[,，。；;！？!\n\t\s]+", text_stripped)
    all_tokens = []
    for c in clauses:
        c = c.strip()
        if not c:
            continue
        for pat in STOP_PATTERNS:
            c = re.sub(pat, " ", c)
        words = [w.strip() for w in c.split() if len(w.strip()) >= 2]
        all_tokens.extend(words)

    if not all_tokens:
        clean_fallback = re.sub(r"[^\w\s\u4e00-\u9fa5a-zA-Z0-9]", " ", text_stripped)
        return {
            "core": clean_fallback[:20].strip(),
            "event": clean_fallback[:20].strip(),
            "refute": f"{clean_fallback[:15].strip()} 辟谣",
        }

    # 优先级过滤：在词数较多时剔除次要通用词
    primary_tokens = [t for t in all_tokens if t not in SECONDARY_WORDS]
    if len(primary_tokens) < 2:
        primary_tokens = all_tokens

    # 严格压缩在 2~3 个核心词内（前 2 个主体词 + 关键动作/状态词）
    if len(primary_tokens) <= 3:
        core = " ".join(primary_tokens)
    else:
        core = f"{primary_tokens[0]} {primary_tokens[1]} {primary_tokens[-1]}"

    event = " ".join(all_tokens[-2:]) if len(all_tokens) >= 2 else core
    if primary_tokens[0] not in event:
        event = f"{primary_tokens[0]} {event}"

    refute = f"{core[:20]} 辟谣"
    return {"core": core, "event": event, "refute": refute}


async def search_news_channel(
    query: str, max_results: int = 3, timelimit: Optional[str] = None
) -> List[Dict[str, Any]]:
    """通道一：权威新闻通讯社与严肃采编报道通道（优先 Tavily News，备用 DDGS News (Bing 后端)）"""
    results: List[Dict[str, Any]] = []

    # 1. 优先使用 Tavily News
    if settings.TAVILY_API_KEY:
        try:
            from tavily import TavilyClient

            client = TavilyClient(api_key=settings.TAVILY_API_KEY)
            resp = await asyncio.to_thread(
                client.search,
                query=query,
                topic="news",
                search_depth="basic",
                max_results=max_results,
            )
            for item in resp.get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    "snippet": item.get("content", ""),
                    "url": item.get("url", ""),
                    "published_date": item.get("published_date", ""),
                    "source": "tavily_news",
                    "channel": "权威新闻通讯社",
                })
            if results:
                return results
        except Exception:
            pass

    # 2. 备用 DuckDuckGo News (针对性使用 bing 后端，国内高可用且时效性强)
    try:
        from ddgs import DDGS

        def _ddg_news():
            with DDGS(timeout=6) as ddgs:
                kwargs: Dict[str, Any] = {"max_results": max_results, "backend": "bing"}
                if timelimit in ("d", "w", "m", "y"):
                    kwargs["timelimit"] = timelimit
                return list(ddgs.news(query, **kwargs))

        items = await asyncio.to_thread(_ddg_news)
        for item in items:
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("body", ""),
                "url": item.get("url", ""),
                "published_date": item.get("date", ""),
                "source": item.get("source", "ddgs_news"),
                "channel": "权威新闻通讯社",
            })
    except Exception:
        pass

    return results


async def search_authoritative_channel(
    query: str, claim_type: str = "hard_data", max_results: int = 2
) -> List[Dict[str, Any]]:
    """通道二：垂直学术顶刊、国际标准与官方通报定向通道"""
    results: List[Dict[str, Any]] = []

    if claim_type == "scientific_tech":
        clean_targeted_query = f"{query} 学术论文 研究"
    elif claim_type == "health_medical":
        clean_targeted_query = f"{query} 官方指南 临床"
    else:
        clean_targeted_query = f"{query} 官方通报 监管"

    # 1. 尝试 Tavily 检索
    if settings.TAVILY_API_KEY:
        try:
            from tavily import TavilyClient

            client = TavilyClient(api_key=settings.TAVILY_API_KEY)
            resp = await asyncio.to_thread(
                client.search,
                query=clean_targeted_query,
                search_depth="basic",
                max_results=max_results,
            )
            for item in resp.get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    "snippet": item.get("content", ""),
                    "url": item.get("url", ""),
                    "published_date": item.get("published_date", ""),
                    "source": "tavily_authority",
                    "channel": "官方与学术顶刊",
                })
            if results:
                return results
        except Exception:
            pass

    # 2. 尝试 DDGS 检索
    try:
        from ddgs import DDGS

        def _ddg_auth():
            with DDGS(timeout=6) as ddgs:
                return list(ddgs.text(clean_targeted_query, backend="bing,yahoo", max_results=max_results))

        items = await asyncio.to_thread(_ddg_auth)
        for item in items:
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("body", ""),
                "url": item.get("href", ""),
                "published_date": item.get("date", ""),
                "source": "ddgs_authority",
                "channel": "官方与学术顶刊",
            })
    except Exception:
        pass

    return results


async def search_encyclopedic_channel(
    query: str, claim_type: str = "hard_data"
) -> List[Dict[str, Any]]:
    """通道三：权威百科与常模参数基准通道（严格准入：仅对理论定义、科学常数与行业基准启用）"""
    results: List[Dict[str, Any]] = []

    # 严格准入拦截：社会突发新闻、案件纠纷、企业舆情绝不查百科，避免无关地理概况污染证据
    allowed_types = {"scientific_tech", "definition", "industry_benchmark"}
    if claim_type not in allowed_types:
        return results

    clean_kw = [w for w in re.sub(r"[^\w\s\u4e00-\u9fa5]", " ", query).split() if len(w) >= 2]
    if not clean_kw:
        return results

    entity = clean_kw[0]
    # 严格拦截通用泛地名词条（如“潮州市”、“广东省”）
    if entity in COMMON_GEOGRAPHIC_ENTITIES or len(entity) < 2:
        return results

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi",
                params={"scope": 103, "format": "json", "appid": 379020, "bk_key": entity},
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                abstract = data.get("abstract", "").strip()
                title = data.get("title", entity)
                url = data.get("url", f"https://baike.baidu.com/item/{entity}")

                if title in COMMON_GEOGRAPHIC_ENTITIES:
                    return results

                if abstract and len(abstract) > 30:
                    results.append({
                        "title": f"【行业概念常模】{title}",
                        "snippet": abstract[:350],
                        "url": url,
                        "published_date": "",
                        "source": "baike_benchmark",
                        "channel": "权威百科基准",
                    })
    except Exception:
        pass

    return results


async def search_open_web(
    query: str,
    max_results: int = 3,
    time_range: Optional[str] = None,
    timelimit: Optional[str] = None,
    refute: bool = False,
) -> List[Dict[str, Any]]:
    """通道四：开放网络高召回探测通道 (正向佐证 vs 反向质疑)"""
    results: List[Dict[str, Any]] = []
    channel_name = "开放网络(质疑/反思)" if refute else "开放网络(正向佐证)"

    # 1. Tavily
    if settings.TAVILY_API_KEY:
        try:
            from tavily import TavilyClient

            client = TavilyClient(api_key=settings.TAVILY_API_KEY)
            kwargs: Dict[str, Any] = {
                "query": query,
                "search_depth": "basic",
                "max_results": max_results,
            }
            if time_range in ("day", "week", "month", "year"):
                kwargs["time_range"] = time_range

            resp = await asyncio.to_thread(client.search, **kwargs)
            for item in resp.get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    "snippet": item.get("content", ""),
                    "url": item.get("url", ""),
                    "published_date": item.get("published_date", ""),
                    "source": "tavily",
                    "channel": channel_name,
                })
            if results:
                return results
        except Exception:
            pass

    # 2. DDGS：采用纯净关键词，由 Python 统一后置过滤黑名单
    try:
        from ddgs import DDGS

        def _ddg_open():
            with DDGS(timeout=6) as ddgs:
                ddg_kwargs: Dict[str, Any] = {"max_results": max_results, "backend": "bing,yahoo"}
                if timelimit in ("d", "w", "m", "y"):
                    ddg_kwargs["timelimit"] = timelimit
                return list(ddgs.text(query, **ddg_kwargs))

        items = await asyncio.to_thread(_ddg_open)
        for item in items:
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("body", ""),
                "url": item.get("href", ""),
                "published_date": item.get("date", ""),
                "source": "duckduckgo",
                "channel": channel_name,
            })
    except Exception:
        pass

    return results


async def search_multi_source_evidence(
    query: str,
    claim_type: str = "hard_data",
    max_results: int = 6,
    time_range: Optional[str] = None,
    timelimit: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """多元独立信息源异构采集矩阵调度器与域名多样性保证器：
    1. 智能提取高信息密度关键词（核心实体、事件处置、反向质疑）；
    2. 并发调度新闻（Bing News）、官方、百科（受控准入）与开放网络多路探测通道；
    3. Python 统一后置剔除内容农场黑名单，并进行权威分级加权与顶级域名多样性去重采样。
    """
    if time_range and not timelimit:
        mapping = {"day": "d", "week": "w", "month": "m", "year": "y"}
        timelimit = mapping.get(time_range)

    # 0. 搜索结果持久化缓存拦截（12小时有效）
    query_clean = query.strip()
    query_hash = hashlib.sha256(f"{query_clean}:{claim_type}:{timelimit}".encode("utf-8")).hexdigest()
    cache_key = f"search_q_v1:{query_hash}"

    try:
        cached = await cache_client.get(cache_key)
        if cached and isinstance(cached.get("items"), list) and cached.get("items"):
            return cached["items"]
    except Exception:
        pass

    # 智能生成多维检索关键词
    kw_dict = extract_search_keywords(query)
    core_q = kw_dict["core"]
    event_q = kw_dict["event"]
    refute_q = kw_dict["refute"]

    # 并发调度各独立通道（干净关键词，零布尔语法，防止超时与频控）
    channel_tasks = [
        search_news_channel(core_q, max_results=3, timelimit=timelimit),
        search_authoritative_channel(core_q, claim_type=claim_type, max_results=2),
        search_encyclopedic_channel(core_q, claim_type=claim_type),
        search_open_web(core_q, max_results=3, time_range=time_range, timelimit=timelimit, refute=False),
        search_open_web(event_q, max_results=2, time_range=time_range, timelimit=timelimit, refute=False),
        search_open_web(refute_q, max_results=2, time_range=time_range, timelimit=timelimit, refute=True),
    ]

    all_channel_results = await asyncio.gather(*channel_tasks, return_exceptions=True)

    raw_candidates: List[Dict[str, Any]] = []
    for res in all_channel_results:
        if isinstance(res, list):
            raw_candidates.extend(res)

    # 清洗与权威等级判定（Python 统一后置过滤内容农场）
    valid_candidates: List[Dict[str, Any]] = []
    seen_urls = set()

    for item in raw_candidates:
        url = item.get("url", "").strip()
        if not url or url in seen_urls:
            continue
        # 拦截低质内容农场
        if is_blacklisted_domain(url):
            continue

        seen_urls.add(url)
        tier_info = classify_source_tier(url)
        item["root_domain"] = tier_info["root_domain"]
        item["authority_tier"] = tier_info["tier"]
        item["tier_label"] = tier_info["tier_label"]
        item["weight"] = tier_info["weight"]
        valid_candidates.append(item)

    # 信源多样性保证器 (Domain Diversity Enforcer)
    # 按权威权重降序排序（优先保留 Tier 1 官方与 Tier 2 主流媒体）
    valid_candidates.sort(key=lambda x: x.get("weight", 0.0), reverse=True)

    final_evidence: List[Dict[str, Any]] = []
    domain_counts: Dict[str, int] = {}

    for item in valid_candidates:
        dom = item.get("root_domain") or "unknown"
        current_count = domain_counts.get(dom, 0)
        max_per_domain = 2 if item.get("authority_tier") in ("tier_1", "tier_2") else 1
        if current_count < max_per_domain:
            domain_counts[dom] = current_count + 1
            final_evidence.append(item)
            if len(final_evidence) >= max_results:
                break

    try:
        if final_evidence:
            await cache_client.set(cache_key, {"items": final_evidence}, ttl=43200)
    except Exception:
        pass

    return final_evidence


async def search_evidence(
    query: str,
    max_results: int = 4,
    time_range: Optional[str] = None,
    timelimit: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """向后兼容的检索入口：统一调度多源异构采集矩阵"""
    return await search_multi_source_evidence(
        query=query,
        claim_type="hard_data",
        max_results=max_results,
        time_range=time_range,
        timelimit=timelimit,
    )
