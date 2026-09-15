import json
from typing import Dict, Any, List
from langchain_core.messages import SystemMessage, HumanMessage
from app.agent.state import DigestState
from app.agent.llm import get_llm, MockLLMService


CLAIM_EXTRACTION_SYSTEM_PROMPT = """你是一个顶级的信息分析与事实核查专家。
你的任务是对给定的文章正文进行“事实与观点分离”：
1. 提炼文章的核心论点骨架。
2. 识别文章中包含的【事实断言 (Factual Claims)】。特别关注以下类型：
   - hard_data: 具体数字、百分比、增长率、突破数据等硬性数据指标
   - health_medical: 医疗健康、疾病治疗、药物疗效等公众关切
   - scientific_tech: 前沿科技突破、重大发明、性能测试
   - breaking_news: 重大突发新闻、人事变动、历史事件
   - general_opinion: 常规主观观点、感想（这类无需核验）
3. 对于带有具体可查事实/硬性数据的断言，标记 need_verify 为 true，并生成 1 个简明精准的用于搜索引擎核验的关键词 search_query；对于主观观点标记 need_verify 为 false。

请务必以纯 JSON 格式输出，不要包含任何 markdown 标记外的闲聊，格式如下：
{
  "claims": [
    {
      "claim_id": "C1",
      "text": "断言原文语句",
      "claim_type": "hard_data",
      "need_verify": true,
      "search_query": "搜索关键词"
    }
  ]
}
"""


async def claim_extract_node(state: DigestState) -> Dict[str, Any]:
    """Node A: 论点与断言抽取"""
    title = state.get("title", "")
    content = state.get("clean_content", "")
    enable_fact_check = state.get("enable_fact_check", True)

    llm = get_llm()
    claims: List[Dict[str, Any]] = []

    if llm:
        try:
            # 截取前 4000 字符用于核心断言提炼
            prompt_content = content[:4000]
            messages = [
                SystemMessage(content=CLAIM_EXTRACTION_SYSTEM_PROMPT),
                HumanMessage(content=f"文章标题：《{title}》\n\n正文内容：\n{prompt_content}"),
            ]
            response = await llm.ainvoke(messages)
            raw_text = response.content.strip()

            # 清理可能的 markdown 代码块
            if "```json" in raw_text:
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_text:
                raw_text = raw_text.split("```")[1].split("```")[0].strip()

            parsed = json.loads(raw_text)
            claims = parsed.get("claims", [])
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
        "message": f"成功抽取 {len(claims)} 项断言，其中 {sum(1 for c in claims if c.get('need_verify'))} 项待核查",
        "claims": claims,
    })

    return {
        "claims": claims,
        "need_fact_check": need_fact_check,
        "events": events,
    }
