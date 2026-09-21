import json
import re
import logging
from typing import Dict, Any
from langchain_core.messages import SystemMessage, HumanMessage
from app.agent.state import DigestState
from app.agent.llm import get_llm, MockLLMService, extract_json_from_llm

logger = logging.getLogger(__name__)

SYNTHESIZE_SYSTEM_PROMPT = """你是一个全能型的内容智能脱水与深度报告合成架构师。
你的核心任务是：深入理解提供的文章正文和事实核验断言，严格遵循以下指定的输出格式规范，由你自主进行高密度的内容提炼、逻辑归纳与智能生成。

【输出格式约束（纯 JSON 输出）】：
请严格以 JSON 格式输出，包含以下三个字段：
{
  "mindmap": "思维导图 Markdown 文本",
  "summary": "结构化脱水摘要 Markdown 文本",
  "final_report": "完整分析长篇报告 Markdown 文本"
}

【各模块具体格式骨架（仅提供格式模板，内容由你根据文章自主智能填充）】：

1. mindmap 格式规范：
以 Markdown 树形层级（# ## - 等）表示全文逻辑骨架：
# <文章标题>
## 一、核心背景与问题界定
- <由AI提取：背景脉络、行业痛点或核心议题（拒绝通用套话，须含具体信息）>
- <由AI提取：关键驱动力或问题本质>
## 二、核心事实论据与技术拆解
- <由AI提取：支撑文章论点的硬核数据与关键事实（结合事实断言及具体数字/指标）>
- <由AI提取：技术参数、对比评估或实测结果>
## 三、关键方案与实施路径
- <由AI提取：核心技术方案、方法论或架构实现逻辑>
- <由AI提取：关键执行步骤与边界条件>
## 四、潜在挑战与未来趋势
- <由AI提取：落地阻碍、工程挑战或核验争议点>
- <由AI提取：阶段性发展演进趋势与结论展望>

2. summary 格式规范：
【核心论点】
<由AI高度凝练：1-2句话直击本文最具信息密度的核心主张，绝不含虚饰套话>

【脱水要点】
1. 核心洞察：<由AI精炼文章最具价值的见解与逻辑主线>
2. 论据支撑：<由AI提炼文中硬核数据、实验结论与事实证据>
3. 实施路径：<由AI提炼具体落地机制、技术路径或推演过程>
4. 价值结论：<由AI提炼深度结论与行业/学术启发>

3. final_report 格式规范：
# 📄 《<文章标题>》智能脱水与深度核验报告

---

## 📌 核心思维导图
```markdown
<插入上述 mindmap 内容>
```

---

## 💡 深度脱水摘要
<插入上述 summary 内容>

---

## 🛡️ 事实断言与动态核查结果
| 断言编号 | 断言原文 | 类型 | 核验评级 | 核查证据与结论 |
| :--- | :--- | :--- | :---: | :--- |
<根据输入的事实断言列表填充表格，核验评级使用 🟢 可信 / 🟡 存疑 / 🔴 违规/虚假 / 🕒 已过时 / ⚪ 免核验(常规观点)>

---

## 🔍 批判性阅读建议
<由AI结合事实核查结果与内容客观性（特别是存疑、过时或数据夸大断言），给出 2-3 条专业审慎的批判性阅读与时效验证建议>
"""


async def synthesize_node(state: DigestState) -> Dict[str, Any]:
    """Node D: 结构化报告合成"""
    title = state.get("title", "未命名内容")
    content = state.get("clean_content", "")
    claims = state.get("claims", [])
    author = state.get("author", "未知作者")
    platform = state.get("platform", "web")

    llm = get_llm()

    if llm:
        try:
            claims_str = json.dumps(claims, ensure_ascii=False, indent=2)
            prompt = (
                f"文章标题：《{title}》\n"
                f"作者：{author} | 平台：{platform}\n\n"
                f"正文前段：\n{content[:4000]}\n\n"
                f"事实断言提取与核查结果：\n{claims_str}\n"
            )
            messages = [
                SystemMessage(content=SYNTHESIZE_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]
            resp = await llm.ainvoke(messages)
            raw_text = resp.content.strip()

            try:
                result = extract_json_from_llm(raw_text)
                mindmap = result.get("mindmap", "")
                summary = result.get("summary", "")
                final_report = result.get("final_report", "")
            except Exception as json_err:
                # 兼容大模型直接输出完整 Markdown 长报告的场景
                if raw_text.startswith("# ") or "## 📌 核心思维导图" in raw_text:
                    logger.info("LLM 直接返回了完整 Markdown 报告格式，进行结构化切分兼容处理")
                    final_report = raw_text
                    # 尝试从 Markdown 提取思维导图与摘要
                    mm_match = re.search(r"## 📌 核心思维导图\s*```markdown\s*([\s\S]*?)\s*```", raw_text)
                    mindmap = mm_match.group(1).strip() if mm_match else ""
                    sm_match = re.search(r"## 💡 深度脱水摘要\s*([\s\S]*?)(?=\n---\n|\n## |\Z)", raw_text)
                    summary = sm_match.group(1).strip() if sm_match else ""
                else:
                    raise json_err
        except Exception as e:
            logger.warning(f"LLM 报告合成执行或解析异常，降级到本地仿真服务: {e}")
            mock_data = MockLLMService.mock_synthesize(title, content, claims)
            mindmap = mock_data["mindmap"]
            summary = mock_data["summary"]
            final_report = mock_data["final_report"]
    else:
        mock_data = MockLLMService.mock_synthesize(title, content, claims)
        mindmap = mock_data["mindmap"]
        summary = mock_data["summary"]
        final_report = mock_data["final_report"]

    events = state.get("events", [])
    events.append({
        "stage": "synthesize",
        "message": "脱水报告与结构化思维导图合成完毕",
    })

    return {
        "mindmap": mindmap,
        "summary": summary,
        "final_report": final_report,
        "events": events,
    }
