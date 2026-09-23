import json
import math
import hashlib
import logging
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter
import numpy as np
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
import jieba

from app.config import get_settings
from app.db.models import Article, DigestRecord

logger = logging.getLogger(__name__)
settings = get_settings()

# 全局本地向量模型单例缓存
_local_embedding_model = None

# 常见形近字与错别字规则映射表 (毫秒级即时纠错)
TYPO_CORRECTIONS = {
    "兵乓球": "乒乓球",
    "兵乓": "乒乓",
    "乒乒球": "乒乓球",
    "冰乓球": "乒乓球",
    "桌球": "乒乓球",
}


def get_local_embedding_model():
    """惰性单例加载本地微型 ONNX 向量引擎 (BAAI/bge-small-zh-v1.5)"""
    global _local_embedding_model
    if _local_embedding_model is None:
        try:
            from fastembed import TextEmbedding

            model_name = settings.EMBEDDING_MODEL_NAME
            if "bge" not in model_name.lower():
                model_name = "BAAI/bge-small-zh-v1.5"
            _local_embedding_model = TextEmbedding(model_name=model_name)
            logger.info(f"本地向量模型 {model_name} 加载成功")
        except Exception as e:
            logger.warning(f"本地 FastEmbed 模型初始化失败，将使用高维 TF-IDF 语义特征: {e}")
            _local_embedding_model = False
    return _local_embedding_model if _local_embedding_model is not False else None


def dense_feature_hash(text: str, dim: int = 512) -> List[float]:
    """高维加权稠密特征向量兜底 (基于 jieba 分词与双重带符号哈希投影，杜绝低维冲突)"""
    vec = np.zeros(dim, dtype=np.float32)
    words = [w for w in jieba.cut_for_search(text) if len(w.strip()) > 0]
    if not words:
        words = [text]
    word_counts = Counter(words)
    for w, count in word_counts.items():
        weight = math.log1p(count)
        h1 = int(hashlib.md5(w.encode("utf-8")).hexdigest(), 16)
        h2 = int(hashlib.sha256(w.encode("utf-8")).hexdigest(), 16)
        idx1 = h1 % dim
        idx2 = h2 % dim
        sign1 = 1.0 if ((h1 >> 8) & 1) else -1.0
        sign2 = 1.0 if ((h2 >> 8) & 1) else -1.0
        vec[idx1] += sign1 * weight
        vec[idx2] += sign2 * weight
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


async def get_embedding(text: str) -> List[float]:
    """生成文本的嵌入向量：
    1. 优先使用已配置且非 DeepSeek 的兼容 OpenAI 规范的外部向量服务；
    2. 若未配置外部向量服务，使用本地高质量 BAAI/bge-small-zh-v1.5 稠密向量引擎；
    3. 异常时自动降级至 512 维高阶分词投影特征。
    """
    if not text or not text.strip():
        return [0.0] * 512

    # 1. 尝试外部 OpenAI 兼容向量端点
    if settings.is_external_embedding_available:
        try:
            from langchain_openai import OpenAIEmbeddings

            api_key = settings.EMBEDDING_API_KEY or settings.OPENAI_API_KEY
            base_url = settings.EMBEDDING_BASE_URL or settings.OPENAI_BASE_URL
            model_name = settings.EMBEDDING_MODEL_NAME

            embeddings = OpenAIEmbeddings(
                openai_api_key=api_key,
                openai_api_base=base_url,
                model=model_name,
            )
            return await embeddings.aembed_query(text)
        except Exception as e:
            logger.warning(f"外部 Embedding API 调用异常，透明降级至本地向量引擎: {e}")

    # 2. 本地 FastEmbed 向量引擎
    model = get_local_embedding_model()
    if model:
        try:
            # fastembed 内部在多核 CPU 上推理极快
            embeddings = list(model.embed([text]))
            if embeddings and len(embeddings) > 0:
                vec = embeddings[0]
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
                return vec.tolist()
        except Exception as e:
            logger.warning(f"本地 FastEmbed 推理异常: {e}")

    # 3. 稳健高维分词投影兜底
    return dense_feature_hash(text, dim=512)


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """计算两个向量的余弦相似度"""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    a = np.array(v1, dtype=np.float32)
    b = np.array(v2, dtype=np.float32)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def build_searchable_text(
    title: str,
    summary: str,
    claims: Optional[list] = None,
) -> str:
    """构建用于检索的高信息密度语义上下文（标题权重提升）"""
    parts = []
    if title and title.strip():
        parts.append(f"【标题】{title.strip()}")
    if summary and summary.strip():
        parts.append(f"【摘要】{summary.strip()}")
    if claims and isinstance(claims, list):
        claim_texts = []
        for c in claims[:5]:
            if isinstance(c, dict) and c.get("claim"):
                claim_texts.append(str(c["claim"]).strip())
            elif isinstance(c, str):
                claim_texts.append(c.strip())
        if claim_texts:
            parts.append("【核心事实论点】" + "；".join(claim_texts))
    return "\n".join(parts)


class SimpleBM25:
    """针对中文优化的内存 BM25 全文打分器"""

    def __init__(self, corpus_tokens: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size = len(corpus_tokens)
        self.doc_lengths = [len(d) for d in corpus_tokens]
        self.avgdl = (sum(self.doc_lengths) / self.corpus_size) if self.corpus_size > 0 else 1.0
        self.doc_freqs = []
        self.df = Counter()
        for doc in corpus_tokens:
            freq = Counter(doc)
            self.doc_freqs.append(freq)
            for w in freq:
                self.df[w] += 1
        self.idf = {}
        for w, freq in self.df.items():
            self.idf[w] = math.log((self.corpus_size - freq + 0.5) / (freq + 0.5) + 1.0)

    def score(self, query_tokens: List[str]) -> List[float]:
        """计算 query 对每个文档的 BM25 原始得分"""
        scores = []
        for i, doc_freq in enumerate(self.doc_freqs):
            score = 0.0
            doc_len = self.doc_lengths[i]
            denom_common = self.k1 * (1 - self.b + self.b * doc_len / self.avgdl)
            for q in query_tokens:
                if q not in doc_freq:
                    continue
                tf = doc_freq[q]
                idf = self.idf.get(q, 0.1)
                score += idf * (tf * (self.k1 + 1)) / (tf + denom_common)
            scores.append(score)
        return scores


async def normalize_and_expand_query(query: str) -> Tuple[str, List[str], Optional[str]]:
    """查询预处理与意图纠错：
    1. 应用形近字快速纠错规则（如“兵乓球”->“乒乓球”）；
    2. 若配置了 LLM，尝试极速意图扩展与近义词补全；
    返回: (标准查询词, 关键词切分列表, 纠错提示文本或None)
    """
    clean_q = query.strip()
    corrected_q = clean_q
    correction_notice = None

    # 1. 规则纠错
    for typo, fix in TYPO_CORRECTIONS.items():
        if typo in corrected_q:
            corrected_q = corrected_q.replace(typo, fix)
            correction_notice = f"已将「{typo}」智能纠正为「{fix}」"

    # 2. 如果配置了可用的 DeepSeek/OpenAI LLM 且输入较短，进行快速语义意图理解
    if settings.OPENAI_API_KEY and len(clean_q) <= 30:
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import SystemMessage, HumanMessage

            llm = ChatOpenAI(
                model=settings.MODEL_NAME,
                openai_api_key=settings.OPENAI_API_KEY,
                openai_api_base=settings.OPENAI_BASE_URL,
                temperature=0.0,
                max_tokens=60,
                timeout=1.5,
            )
            prompt = (
                "你是中文搜索意图专家。请针对检索词完成错别字纠错并输出2-4个高相关近义核心实体词，用空格分隔。"
                "不要输出任何多余废话或解释。例如输入'兵乓球'，输出'乒乓球 国乒 桌球'。"
            )
            res = await asyncio.wait_for(
                llm.ainvoke([SystemMessage(content=prompt), HumanMessage(content=clean_q)]),
                timeout=1.8,
            )
            ext_words = res.content.strip().split()
            if ext_words:
                primary = ext_words[0]
                if primary != clean_q and clean_q not in ext_words:
                    corrected_q = primary
                    if not correction_notice:
                        correction_notice = f"已将「{clean_q}」智能纠正为「{primary}」"
                # 融合切词列表
                expanded_tokens = list(set([corrected_q] + ext_words))
                tokens = []
                for w in expanded_tokens:
                    tokens.extend(jieba.cut_for_search(w))
                return corrected_q, list(set(tokens)), correction_notice
        except Exception:
            pass

    # 本地分词切分
    q_tokens = [w for w in jieba.cut_for_search(corrected_q) if len(w.strip()) > 0]
    return corrected_q, q_tokens, correction_notice


async def save_digest_record(
    session: AsyncSession,
    article_id: int,
    summary: str,
    mindmap: str,
    claims: list,
    final_report: str,
    title: Optional[str] = None,
) -> DigestRecord:
    """保存精读记录并基于（标题+摘要+关键论点）综合计算高精度语义向量"""
    if not title:
        stmt = select(Article.title).where(Article.id == article_id)
        res = await session.execute(stmt)
        title = res.scalar_one_or_none() or ""

    searchable_text = build_searchable_text(title=title, summary=summary, claims=claims)
    embedding_vec = await get_embedding(searchable_text)

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


async def reindex_legacy_records_if_needed(session: AsyncSession, rows: list) -> None:
    """检查并自动平滑升级旧版 256 维失效哈希记录"""
    to_update = []
    for record, article in rows:
        try:
            if not record.embedding:
                to_update.append((record, article))
                continue
            vec = json.loads(record.embedding)
            # 旧版伪哈希维度为 256，或包含纯 0 向量，需重建
            if len(vec) == 256 or (len(vec) > 0 and max(vec) == 0.0 and min(vec) == 0.0):
                to_update.append((record, article))
        except Exception:
            to_update.append((record, article))

    if not to_update:
        return

    logger.info(f"正在自动升级迁移 {len(to_update)} 条历史摘要向量记录...")
    for rec, art in to_update:
        try:
            text = build_searchable_text(
                title=art.title,
                summary=rec.summary,
                claims=rec.claims_json if hasattr(rec, "claims_json") else None,
            )
            new_vec = await get_embedding(text)
            rec.embedding = json.dumps(new_vec)
        except Exception as e:
            logger.warning(f"记录 id={rec.id} 升级失败: {e}")

    try:
        await session.commit()
        logger.info("历史记录向量重建与平滑升级完成")
    except Exception as e:
        logger.warning(f"历史记录提交更新失败: {e}")


async def search_similar_digests(
    session: AsyncSession,
    query: str,
    top_k: int = 5,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """混合检索历史精读报告：
    - 查询意图预处理与错别字纠错（如 兵乓球 -> 乒乓球）；
    - BM25 稀疏全文精准检索（标题 3 倍权重加权）；
    - 稠密语义向量余弦相似度匹配；
    - 双路融合评分与置信度阈值过滤（低于门槛直接排除无关项，绝不强行返回噪音）。
    """
    clean_query = query.strip()
    if not clean_query:
        return [], None

    # 1. 查询意图解析与纠错
    normalized_q, q_tokens, notice = await normalize_and_expand_query(clean_query)

    # 2. 查询全部包含关联文章的记录
    stmt = (
        select(DigestRecord, Article)
        .join(Article, DigestRecord.article_id == Article.id)
    )
    result = await session.execute(stmt)
    rows = result.all()

    if not rows:
        return [], notice

    # 3. 自动平滑升级旧版记录
    await reindex_legacy_records_if_needed(session, rows)

    # 4. 生成规范化查询词的语义向量
    query_vec = await get_embedding(normalized_q)

    # 5. 构建 BM25 倒排索引
    corpus_tokens = []
    doc_meta = []
    for record, article in rows:
        title = article.title or ""
        summary = record.summary or ""
        title_tokens = [w for w in jieba.cut_for_search(title) if len(w.strip()) > 0]
        summary_tokens = [w for w in jieba.cut_for_search(summary) if len(w.strip()) > 0]
        # 标题赋予 3 倍权重以强化实体与主题召回
        doc_words = title_tokens * 3 + summary_tokens
        corpus_tokens.append(doc_words)
        doc_meta.append((record, article))

    bm25 = SimpleBM25(corpus_tokens)
    bm25_raw_scores = bm25.score(q_tokens)
    max_bm25 = max(bm25_raw_scores) if bm25_raw_scores else 0.0

    scored_results = []
    for idx, (record, article) in enumerate(doc_meta):
        try:
            doc_vec = json.loads(record.embedding) if record.embedding else []
            dense_sim = cosine_similarity(query_vec, doc_vec)
        except Exception:
            dense_sim = 0.0

        # 归一化 BM25 分数
        raw_bm25 = bm25_raw_scores[idx]
        bm25_norm = (raw_bm25 / max_bm25) if max_bm25 > 0 else 0.0

        # 检查标题中是否包含核心检索词
        title = article.title or ""
        title_exact = any(tok in title for tok in q_tokens if len(tok) >= 2) or (normalized_q in title)
        title_bonus = 0.20 if title_exact else 0.0

        # 综合融合公式 (Hybrid Fusion)
        # 若有精确标题命中或高 BM25，以关键词打分占主导；若为泛化自然语言问题，以向量语义占主导
        if bm25_norm > 0 or title_exact:
            combined_score = 0.50 * bm25_norm + 0.35 * dense_sim + title_bonus
        else:
            combined_score = 0.85 * dense_sim

        combined_score = min(max(combined_score, 0.0), 1.0)

        # 解释匹配原因
        reasons = []
        if title_exact:
            reasons.append("标题精准命中")
        if bm25_norm > 0.3:
            reasons.append("全文关键词强相关")
        if dense_sim > 0.55:
            reasons.append("深层语义高度吻合")
        match_reason = "、".join(reasons) if reasons else "语义弱相关"

        scored_results.append({
            "id": record.id,
            "url": article.url,
            "title": article.title,
            "source_platform": article.platform,
            "summary": record.summary,
            "similarity": round(combined_score, 4),
            "match_reason": match_reason,
            "created_at": record.created_at.isoformat() if record.created_at else "",
            "_raw_bm25": raw_bm25,
            "_dense_sim": dense_sim,
        })

    # 6. 过滤置信度阈值：彻底杜绝“腊味厂”等零相关度无关噪声冒充返回
    threshold = getattr(settings, "EMBEDDING_THRESHOLD", 0.30)
    filtered_results = [
        r for r in scored_results
        if r["similarity"] >= threshold and (r["_raw_bm25"] > 0 or r["_dense_sim"] >= 0.45)
    ]

    # 按综合相似度降序排列
    filtered_results.sort(key=lambda x: x["similarity"], reverse=True)
    return filtered_results[:top_k], notice
