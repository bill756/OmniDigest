import json
import math
import hashlib
from typing import List, Dict, Any, Optional
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import get_settings
from app.db.models import Article, DigestRecord

settings = get_settings()


async def get_embedding(text: str) -> List[float]:
    """生成文本的嵌入向量，若无 API Key 则采用确定性稠密特征向量模拟"""
    if settings.OPENAI_API_KEY:
        try:
            from langchain_openai import OpenAIEmbeddings

            embeddings = OpenAIEmbeddings(
                openai_api_key=settings.OPENAI_API_KEY,
                openai_api_base=settings.OPENAI_BASE_URL,
                model=settings.EMBEDDING_MODEL_NAME,
            )
            return await embeddings.aembed_query(text)
        except Exception as e:
            # 日志降级
            pass

    # 无 Key 或网络调用异常时的兜底稠密向量 (维度 256)
    dim = 256
    vec = np.zeros(dim, dtype=np.float32)
    words = [w for w in text.split() if w] or [text]
    for w in words:
        h = int(hashlib.md5(w.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if ((h >> 8) & 1) else -1.0
        vec[idx] += sign
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """计算两个向量的余弦相似度"""
    a = np.array(v1, dtype=np.float32)
    b = np.array(v2, dtype=np.float32)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


async def save_digest_record(
    session: AsyncSession,
    article_id: int,
    summary: str,
    mindmap: str,
    claims: list,
    final_report: str,
) -> DigestRecord:
    """保存脱水记录并计算摘要向量"""
    embedding_vec = await get_embedding(summary)
    record = DigestRecord(
        article_id=article_id,
        summary=summary,
        mindmap=mindmap,
        claims_json=claims,
        final_report=final_report,
        embedding=json.dumps(embedding_vec),
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def search_similar_digests(
    session: AsyncSession,
    query: str,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """在数据库中基于余弦相似度检索相关文章摘要"""
    query_vec = await get_embedding(query)

    # 查询所有包含 embedding 的记录
    stmt = (
        select(DigestRecord, Article)
        .join(Article, DigestRecord.article_id == Article.id)
        .where(DigestRecord.embedding.isnot(None))
    )
    result = await session.execute(stmt)
    rows = result.all()

    scored_results = []
    for record, article in rows:
        try:
            doc_vec = json.loads(record.embedding)
            sim = cosine_similarity(query_vec, doc_vec)
            scored_results.append({
                "id": record.id,
                "url": article.url,
                "title": article.title,
                "source_platform": article.platform,
                "summary": record.summary,
                "similarity": round(sim, 4),
                "created_at": record.created_at.isoformat() if record.created_at else "",
            })
        except Exception:
            continue

    # 按相似度降序排列
    scored_results.sort(key=lambda x: x["similarity"], reverse=True)
    return scored_results[:top_k]
