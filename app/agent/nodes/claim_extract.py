import json
import asyncio
import re
from datetime import datetime
from typing import Dict, Any, List
from langchain_core.messages import SystemMessage, HumanMessage
from app.agent.state import DigestState
from app.agent.llm import get_llm, MockLLMService, extract_json_from_llm
from app.core.nlp import (
    extract_topic_entities,
    split_sentences,
    is_pure_subjective,
    clean_event_topic,
    ground_claim_with_antecedent,
)


CLAIM_EXTRACTION_SYSTEM_PROMPT = """你是一个专业的新闻分析与事实核查专家。
你的任务是从给定的文章正文中，全面、精准提取高信息密度、自洽完整的【客观事实断言 (Factual Claims)】。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【核心提取准则】：
1. 🎯【客观事实优先，高覆盖率抽取】：
   - 全面提取文章中包含的时间、地点、核心主体行为、警方/官方通报、死伤数据、核心量化指标、案情调查进展等硬事实。
   - 坚决排除纯主观抒情感慨（如“太痛心了”、“这令人深思”）、设问反问（如“如何看待？”）以及无事实依据的个人推论。
2. 🔍【核心主题聚焦，过滤修辞类比（重要铁律）】：
   - 必须紧密围绕本文【核心事件主体】提取直接相关的客观事实！
   - 严禁提取作者为了论述分析而借题发挥引用的历史典故、外围类比或国外案例（例如：文章分析本土案件时，随口对比引用的山上彻也、肯尼迪遇刺、其他历史事件等），这些属于作者个人的修辞性论据，绝不可提取为本文的新闻事实断言。
3. 🧩【语义共指消解与完整交代前因后果 (Coreference Resolution)】：
   - 🛑【严禁无前因指示代词（如“该35岁男性作案后...”）】：
     绝对禁止在断言中留下如“该35岁男性”、“该男子”、“该嫌疑人”、“作案后”、“其行为”等指代不明、缺少案由前因的半截短句！
     必须完整交代【案由背景 + 主体全称】：
     - ❌ 错误孤立断言：“该35岁男性作案后吃了一碗煲仔饭，并在原地等待警方。”（未交代是哪起案件、哪个嫌疑人）
     - ✅ 正确自洽断言：“广州大学城持刀伤人案中，涉案35岁男性嫌疑人在行凶后吃了一碗煲仔饭，并在现场原地等待警方。”
   - 检验标准：任何断言独立脱离文章呈现时，必须具备明确的【事件前因 + 责任主体 + 具体行为/事实】。
   - original_quote 字段请保留文章对应的原文片段。
4. 🏷️【事实分类与核验标记】：
   - claim_type 可选：breaking_news / hard_data / health_medical / scientific_tech
   - 对于客观可查的事实，标记 need_verify 为 true，并生成包含核心实体的 2~3 个关键词作为 search_query（如“广州大学城 持刀伤人 伤亡人数”）。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请务必以纯 JSON 格式输出：
{
  "claims": [
    {
      "claim_id": "C1",
      "text": "通顺自洽的独立陈述句",
      "original_quote": "文章中的对应原文片段",
      "claim_type": "breaking_news",
      "need_verify": true,
      "search_query": "核心主体 动作结果"
    }
  ]
}
"""


def _split_into_chunks(text: str, chunk_size: int = 3500, overlap: int = 300) -> List[str]:
    """对长文本进行带重叠的段落安全切分，避免硬截断导致断言丢失"""
    if len(text) <= chunk_size:
        return [text]

    paragraphs = text.split("\n")
    chunks: List[str] = []
    current_chunk = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para) + 1
        if current_len + para_len > chunk_size and current_chunk:
            chunk_text = "\n".join(current_chunk).strip()
            if chunk_text:
                chunks.append(chunk_text)
            overlap_lines = []
            overlap_len = 0
            for p in reversed(current_chunk):
                if overlap_len + len(p) < overlap:
                    overlap_lines.insert(0, p)
                    overlap_len += len(p)
                else:
                    break
            current_chunk = overlap_lines
            current_len = overlap_len

        current_chunk.append(para)
        current_len += para_len

    if current_chunk:
        chunk_text = "\n".join(current_chunk).strip()
        if chunk_text:
            chunks.append(chunk_text)

    return chunks or [text[:chunk_size]]


async def claim_extract_node(state: DigestState) -> Dict[str, Any]:
    """Node A: 论点与断言抽取 (NLP 结构化初筛 + 语义共指消解与自洽断言重构)"""
    title = state.get("title", "")
    content = state.get("clean_content", "")
    enable_fact_check = state.get("enable_fact_check", True)
    publish_date = state.get("publish_date") or "未知"
    current_date = datetime.now().strftime("%Y-%m-%d")

    # 1. NLP 预处理：提取文章的核心事件实体集，用于引导大模型过滤无关修辞类比
    topic_entities = extract_topic_entities(title, content, top_k=6)
    topic_summary = "、".join(topic_entities) if topic_entities else (title or "文章核心事件")

    llm = get_llm()
    claims: List[Dict[str, Any]] = []

    if llm:
        try:
            chunks = _split_into_chunks(content, chunk_size=3500, overlap=300)

            async def _extract_chunk(chunk_text: str, chunk_idx: int) -> List[Dict[str, Any]]:
                prompt_content = (
                    f"【断言抽取任务】：请对以下文章片段进行客观事实断言提取与共指消解重写。\n"
                    f"【文章标题】：{title}\n"
                    f"【核心事件主体】：{topic_summary}\n"
                    f"【正文片段（第{chunk_idx+1}部分）】：\n{chunk_text}\n\n"
                    f"【时间参考】：发表日期={publish_date}，当前核验系统时间={current_date}"
                )
                messages = [
                    SystemMessage(content=CLAIM_EXTRACTION_SYSTEM_PROMPT),
                    HumanMessage(content=prompt_content),
                ]
                resp = await llm.ainvoke(messages)
                parsed = extract_json_from_llm(resp.content.strip())
                return parsed.get("claims", [])

            target_chunks = chunks[:4]
            if len(target_chunks) == 1:
                raw_claims = await _extract_chunk(target_chunks[0], 0)
            else:
                chunk_results = await asyncio.gather(*[_extract_chunk(c, i) for i, c in enumerate(target_chunks)], return_exceptions=True)
                raw_claims = []
                for res in chunk_results:
                    if isinstance(res, list):
                        raw_claims.extend(res)

            # 聚合与语义去重（基于 text 前缀/核心内容去重，并执行前因消歧兜底）
            clean_event = clean_event_topic(title, topic_entities)
            primary_entity = topic_entities[0] if topic_entities else clean_event
            seen_texts = set()
            idx = 1
            for c in raw_claims:
                txt = c.get("text", "").strip()
                if not txt:
                    continue

                # 自动消解指示代词与补齐事件前因
                txt = ground_claim_with_antecedent(txt, title, topic_entities)
                c["text"] = txt

                # 确保 search_query 包含核心事件主体，防止检索漂移
                sq = c.get("search_query", "").strip()
                if sq:
                    if primary_entity and primary_entity not in sq and clean_event not in sq:
                        c["search_query"] = f"{primary_entity} {sq}"
                else:
                    c["search_query"] = f"{primary_entity} {txt[:15]}"

                norm_key = txt[:30]
                if norm_key not in seen_texts:
                    seen_texts.add(norm_key)
                    c["claim_id"] = f"C{idx}"
                    claims.append(c)
                    idx += 1
        except Exception:
            claims = MockLLMService.extract_claims(content, title)
    else:
        claims = MockLLMService.extract_claims(content, title)

    # 确定是否需要触发联网核验
    has_verifiable = any(c.get("need_verify", False) for c in claims)
    need_fact_check = enable_fact_check and has_verifiable

    events = state.get("events", [])
    events.append({
        "stage": "claim_extract",
        "message": f"成功抽取 {len(claims)} 项自洽事实断言，其中 {sum(1 for c in claims if c.get('need_verify'))} 项待核查",
        "claims": claims,
    })

    return {
        "claims": claims,
        "need_fact_check": need_fact_check,
        "events": events,
    }
