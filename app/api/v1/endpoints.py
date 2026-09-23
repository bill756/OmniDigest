import asyncio
import json
import hashlib
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.config import get_settings
from app.core.security import verify_api_key, validate_url
from app.core.sse import format_sse
from app.schemas.digest import (
    DigestRequest,
    DigestResponse,
    ClaimItem,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)
from app.crawler.dispatcher import crawler_dispatcher
from app.crawler.base import ArticleContent
from app.agent.state import DigestState
from app.agent.graph import digest_graph
from app.db.session import get_db
from app.db.models import Article, DigestRecord
from app.db.vector_store import save_digest_record, search_similar_digests
from app.api.v1.deps import get_cache, CacheClient

router = APIRouter()
settings = get_settings()


async def run_pipeline(
    req: DigestRequest,
    db: AsyncSession,
    cache: CacheClient,
    is_stream: bool = False,
):
    """通用深度精读执行流水线，支持流式生成器产出"""
    validated_url = validate_url(req.url)
    url_hash = cache.get_url_hash(validated_url)
    if req.content_override:
        content_hash = hashlib.sha256(req.content_override.strip().encode("utf-8")).hexdigest()
        cache_key = f"digest_v2:{url_hash}:{content_hash[:16]}:{req.enable_fact_check}"
    else:
        cache_key = f"digest_v2:{url_hash}:{req.enable_fact_check}"

    # 1. 缓存拦截检查
    if not req.force_refresh:
        cached_data = await cache.get(cache_key) or await cache.get(url_hash)
        if cached_data:
            if is_stream:
                yield format_sse({"stage": "cache_hit", "message": "命中热点持久化缓存，毫秒级直接呈现"}, event="status")
                # 模拟极速打字机输出已缓存的报告
                chunks = cached_data.get("final_report", "").split("\n")
                for c in chunks:
                    yield format_sse(c + "\n", event="chunk")
                    await asyncio.sleep(0.01)
                yield format_sse(cached_data, event="result")
                yield format_sse("done", event="done")
                return
            else:
                cached_data["cached"] = True
                yield cached_data
                return

    # 2. 爬虫抓取与正文清洗
    if is_stream:
        yield format_sse({"stage": "crawler", "message": f"正在从 {validated_url} 调度专有解析器抓取正文..."}, event="status")

    if req.content_override:
        article_data = ArticleContent(
            url=validated_url,
            title="自定义录入内容",
            content=req.content_override,
            author="用户录入",
            platform="custom",
        )
    else:
        try:
            article_data = await crawler_dispatcher.crawl(validated_url)
        except Exception as e:
            err_msg = str(e)
            if is_stream:
                yield format_sse({"stage": "crawler_failed", "message": f"抓取受阻: {err_msg}", "error": err_msg}, event="status")
                yield format_sse({"error": err_msg}, event="error")
                yield format_sse("done", event="done")
                return
            raise HTTPException(status_code=502, detail=err_msg)

    if not article_data.content:
        err_msg = "抓取成功但正文为空，可能存在防爬验证码或权限限制"
        if is_stream:
            yield format_sse({"stage": "crawler_failed", "message": f"抓取受阻: {err_msg}", "error": err_msg}, event="status")
            yield format_sse({"error": err_msg}, event="error")
            yield format_sse("done", event="done")
            return
        raise HTTPException(status_code=422, detail=err_msg)

    token_count = len(article_data.content)

    # 3. 文本预处理与长度决策
    if is_stream:
        yield format_sse(
            {"stage": "preprocessor", "message": f"正文清洗完毕 (约 {token_count} 字符)，进入精读分析流水线..."},
            event="status",
        )

    # 4. 执行 LangGraph 工作流
    initial_state: DigestState = {
        "url": validated_url,
        "title": article_data.title,
        "author": article_data.author,
        "publish_date": article_data.publish_date,
        "platform": article_data.platform,
        "clean_content": article_data.content,
        "token_count": token_count,
        "enable_fact_check": req.enable_fact_check,
        "events": [],
    }

    if is_stream:
        # 渐进推流
        final_state = dict(initial_state)
        async for output in digest_graph.astream(initial_state):
            for node_name, node_output in output.items():
                final_state.update(node_output)
                if node_name == "claim_extract":
                    claims = node_output.get("claims", [])
                    need_check = node_output.get("need_fact_check", False)
                    msg = f"断言分析完成：发现 {len(claims)} 个事实点，{'触发联网核验' if need_check else '无需核验'}"
                    yield format_sse({"stage": "claim_extract", "message": msg, "claims": claims}, event="status")
                elif node_name == "fact_check":
                    yield format_sse({
                        "stage": "fact_check",
                        "message": "联网交叉比对完成，证据已反哺",
                        "claims": node_output.get("claims", []),
                    }, event="status")
                elif node_name == "synthesize":
                    yield format_sse({"stage": "synthesize", "message": "正在渲染最终精读报告..."}, event="status")
    else:
        final_state = await digest_graph.ainvoke(initial_state)

    # 5. 持久化至数据库 (Article + DigestRecord)
    try:
        # 查询或创建 Article
        stmt = select(Article).where(Article.url == validated_url)
        res = await db.execute(stmt)
        db_article = res.scalar_one_or_none()

        if not db_article:
            db_article = Article(
                url=validated_url,
                url_hash=url_hash,
                title=article_data.title,
                author=article_data.author,
                publish_date=article_data.publish_date,
                platform=article_data.platform,
                clean_content=article_data.content,
                token_count=token_count,
            )
            db.add(db_article)
            await db.commit()
            await db.refresh(db_article)

        record = await save_digest_record(
            session=db,
            article_id=db_article.id,
            summary=final_state.get("summary", ""),
            mindmap=final_state.get("mindmap", ""),
            claims=final_state.get("claims", []),
            final_report=final_state.get("final_report", ""),
            title=article_data.title,
        )
    except Exception as e:
        # 记录日志，不阻断输出
        pass

    # 6. 回填 Redis 缓存
    response_payload = {
        "url": validated_url,
        "title": article_data.title,
        "author": article_data.author,
        "publish_date": article_data.publish_date,
        "source_platform": article_data.platform,
        "cached": False,
        "token_count": token_count,
        "mindmap": final_state.get("mindmap", ""),
        "summary": final_state.get("summary", ""),
        "claims": final_state.get("claims", []),
        "final_report": final_state.get("final_report", ""),
        "created_at": record.created_at.isoformat() if "record" in locals() else "",
    }
    await cache.set(cache_key, response_payload, ttl=settings.REDIS_CACHE_TTL)
    await cache.set(url_hash, response_payload, ttl=settings.REDIS_CACHE_TTL)

    # 7. 流式逐 token 输出或直接返回
    if is_stream:
        # 逐段模拟流式打字机输出 report
        report_text = final_state.get("final_report", "")
        for line in report_text.splitlines(keepends=True):
            yield format_sse(line, event="chunk")
            await asyncio.sleep(0.015)
        yield format_sse(response_payload, event="result")
        yield format_sse("done", event="done")
    else:
        yield response_payload


@router.post(
    "/digest",
    summary="对指定文章 URL 进行深度精读与事实核验",
    response_model=DigestResponse,
)
async def create_digest(
    req: DigestRequest,
    request: Request,
    stream: bool = Query(default=False, description="是否开启 SSE 流式输出"),
    db: AsyncSession = Depends(get_db),
    cache: CacheClient = Depends(get_cache),
    _: bool = Depends(verify_api_key),
):
    """
    核心 API: 支持微信公众号、知乎专栏以及通用网页
    客户端支持通过 ?stream=true 或请求头 Accept: text/event-stream 开启打字机渐进式推流。
    """
    accept = request.headers.get("accept", "")
    wants_stream = stream or ("text/event-stream" in accept)

    if wants_stream:
        return StreamingResponse(
            run_pipeline(req, db, cache, is_stream=True),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # 非流式直接等待结果
    gen = run_pipeline(req, db, cache, is_stream=False)
    async for res in gen:
        return res


@router.get(
    "/history",
    summary="获取历史精读报告列表",
)
async def list_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_api_key),
):
    stmt = (
        select(DigestRecord, Article)
        .join(Article, DigestRecord.article_id == Article.id)
        .order_by(desc(DigestRecord.created_at))
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    records = []
    for record, article in result.all():
        records.append({
            "id": record.id,
            "article_id": article.id,
            "url": article.url,
            "title": article.title,
            "platform": article.platform,
            "summary": record.summary,
            "created_at": record.created_at.isoformat() if record.created_at else "",
        })
    return {"total": len(records), "records": records}


@router.post(
    "/search",
    summary="基于混合检索跨文章检索历史报告",
    response_model=SearchResponse,
)
async def search_digests(
    req: SearchRequest,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_api_key),
):
    results, notice = await search_similar_digests(db, query=req.query, top_k=req.top_k)
    return SearchResponse(
        query=req.query,
        notice=notice,
        total=len(results),
        results=[SearchResultItem(**r) for r in results],
    )


@router.get(
    "/article/{article_id}",
    summary="根据 ID 获取文章原正文及最新精读报告",
)
async def get_article_detail(
    article_id: int,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_api_key),
):
    stmt = (
        select(Article, DigestRecord)
        .outerjoin(DigestRecord, Article.id == DigestRecord.article_id)
        .where(Article.id == article_id)
        .order_by(desc(DigestRecord.created_at))
    )
    res = await db.execute(stmt)
    row = res.first()
    if not row:
        raise HTTPException(status_code=404, detail="文章未找到")

    article, record = row
    return {
        "id": article.id,
        "url": article.url,
        "title": article.title,
        "author": article.author,
        "platform": article.platform,
        "clean_content": article.clean_content,
        "token_count": article.token_count,
        "summary": record.summary if record else None,
        "mindmap": record.mindmap if record else None,
        "claims": record.claims_json if record else None,
        "final_report": record.final_report if record else None,
        "created_at": article.created_at.isoformat() if article.created_at else "",
    }
