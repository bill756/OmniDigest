import json
import re
from typing import Optional, List, Dict, Any, AsyncGenerator
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from app.config import get_settings

settings = get_settings()


def get_llm(streaming: bool = False) -> Optional[ChatOpenAI]:
    """获取配置好的 ChatOpenAI 客户端"""
    if not settings.OPENAI_API_KEY:
        return None

    return ChatOpenAI(
        model=settings.MODEL_NAME,
        temperature=settings.TEMPERATURE,
        openai_api_key=settings.OPENAI_API_KEY,
        openai_api_base=settings.OPENAI_BASE_URL,
        streaming=streaming,
    )


def extract_json_from_llm(raw_text: str) -> Any:
    """从 LLM 响应文本中稳健提取 JSON 数据。

    彻底解决因 LLM 在字段值内包含 Markdown 代码块（如 ```markdown ... ```）时，
    朴素的 .split("```")[0] 导致字段被恶意腰斩、引发 Expecting value: line 1 column 1 的严重缺陷。
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("LLM 返回内容为空")

    text = raw_text.strip()

    # 1. 尝试直接反序列化
    try:
        return json.loads(text, strict=False)
    except Exception:
        pass

    # 2. 剥离首尾的代码块标记（仅剥离最外层首尾匹配的 ```json 与 ```）
    cleaned = text
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:].strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:].strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

    try:
        return json.loads(cleaned, strict=False)
    except Exception:
        pass

    # 3. 提取最外层的完整 JSON 对象 { ... }
    start_brace = cleaned.find("{")
    end_brace = cleaned.rfind("}")
    if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
        candidate = cleaned[start_brace : end_brace + 1]
        try:
            return json.loads(candidate, strict=False)
        except Exception:
            pass

    # 4. 提取最外层的完整 JSON 列表 [ ... ]
    start_bracket = cleaned.find("[")
    end_bracket = cleaned.rfind("]")
    if start_bracket != -1 and end_bracket != -1 and end_bracket > start_bracket:
        candidate = cleaned[start_bracket : end_bracket + 1]
        try:
            return json.loads(candidate, strict=False)
        except Exception:
            pass

    # 5. 正则贪婪匹配
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if match:
        try:
            return json.loads(match.group(0), strict=False)
        except Exception:
            pass

    raise json.JSONDecodeError(f"无法从大模型响应中解析出有效的 JSON 数据: {text[:100]}...", text, 0)


class MockLLMService:
    """当未配置真实 OPENAI_API_KEY 时的仿真智能降级服务，确保开发测试零门槛"""

    @staticmethod
    def extract_claims(text: str, title: str) -> List[Dict[str, Any]]:
        # 启发式识别数字、百分比、极端词或专业术语
        lines = [line.strip() for line in text.split("\n") if len(line.strip()) > 15]
        claims = []
        claim_idx = 1

        for line in lines[:8]:
            # 匹配数字、百分比或技术断言
            has_data = bool(re.search(r"\d+(\.\d+)?%?|倍|突破|首个|唯一|显著|万|亿", line))
            claim_type = "hard_data" if has_data else "general_opinion"
            need_verify = has_data  # 解除限制，对全部硬性数据断言均标记核查

            claims.append({
                "claim_id": f"C{claim_idx}",
                "text": line[:120],
                "claim_type": claim_type,
                "need_verify": need_verify,
                "search_query": f"{title} {line[:30]}",
                "confidence_level": "unverified",
                "verification_reason": None,
                "sources": [],
            })
            claim_idx += 1

        if not claims:
            claims.append({
                "claim_id": "C1",
                "text": text[:100],
                "claim_type": "general_opinion",
                "need_verify": False,
                "search_query": title,
                "confidence_level": "unverified",
                "verification_reason": None,
                "sources": [],
            })

        return claims

    @staticmethod
    def mock_synthesize(
        title: str,
        content: str = "",
        claims: Optional[List[Dict[str, Any]]] = None,
        summary: Optional[str] = None,
    ) -> Dict[str, str]:
        """仿真智能脱水与结构化报告合成：
        根据文章实际内容与断言核查结果动态构建思维导图、脱水摘要与完整报告，避免固定模板式输出。
        """
        text = (content or summary or "").strip()
        claims = claims or []

        # 1. 文本清洗与分句
        raw_lines = [line.strip() for line in text.split("\n") if line.strip()]
        noise_keywords = ["关注", "免责声明", "未经授权", "版权所有", "点击上方", "长按二维码", "来源：", "作者："]
        clean_lines = [
            l for l in raw_lines
            if not any(k in l for k in noise_keywords)
        ]

        sentences: List[str] = []
        for line in clean_lines:
            parts = re.split(r"[。！？!?；;\n]+", line)
            for p in parts:
                p = p.strip()
                # 剔除开头常见的标签前缀，如 【重磅】、[原创] 等
                p_clean = re.sub(r"^[【\[][^】\]]+[】\]]", "", p).strip()
                if len(p_clean) >= 6 and not re.match(r"^[-*#\d\.\s]+$", p_clean):
                    if p_clean not in sentences:
                        sentences.append(p_clean)

        def is_question(s: str) -> bool:
            return s.endswith(("?", "？")) or bool(re.match(r"^(如何|怎样|怎么|为什么|什么是|是否)", s))

        def trim(s: str, max_len: int = 50) -> str:
            s = re.sub(r"^[【\[][^】\]]+[】\]]", "", s).strip()
            return s[:max_len] + ("..." if len(s) > max_len else "")

        # 2. 识别核心论点与开篇议题（优先识别设问句与核心首句）
        used_sentences = set()
        intro_points: List[str] = []

        if sentences:
            if is_question(sentences[0]) and len(sentences) > 1:
                intro_points.append(f"问题探讨：{trim(sentences[0], 45)}")
                intro_points.append(f"核心论断：{trim(sentences[1], 55)}")
                used_sentences.add(sentences[0])
                used_sentences.add(sentences[1])
                core_thesis = sentences[1]
            else:
                intro_points.append(trim(sentences[0], 55))
                used_sentences.add(sentences[0])
                core_thesis = sentences[0]
                if len(sentences) > 1 and sentences[1] not in used_sentences:
                    has_hard_data = bool(re.search(r"\d+(\.\d+)?%?|倍|突破|首个|唯一|显著|万|亿|千", sentences[1]))
                    if not has_hard_data:
                        intro_points.append(trim(sentences[1], 55))
                        used_sentences.add(sentences[1])
        else:
            core_thesis = f"系统剖析「{title}」的核心发展脉络与技术机理"
            intro_points.append(core_thesis)

        # 3. 提取核心事实与数据论据（优先 hard_data 或带数值断言）
        fact_points: List[str] = []
        if claims:
            # 过滤纯设问句，优先排序含有数据的硬断言
            sorted_claims = sorted(
                claims,
                key=lambda x: (
                    1 if x.get("claim_type") == "hard_data" else 2,
                    1 if not is_question(x.get("text", "")) else 3,
                ),
            )
            for c in sorted_claims:
                txt = trim(c.get("text", ""), 60)
                if not txt or is_question(txt):
                    continue
                badge = "硬核数据" if c.get("claim_type") == "hard_data" else "关键断言"
                fact_points.append(f"【{badge}】{txt}")
                if len(fact_points) >= 3:
                    break

        if not fact_points:
            data_s = [
                s for s in sentences
                if re.search(r"\d+(\.\d+)?%?|倍|突破|首个|唯一|显著|万|亿|千|提升|降低", s)
            ]
            for s in data_s[:3]:
                fact_points.append(f"【关键数据】{trim(s, 60)}")
                used_sentences.add(s)

            if not fact_points:
                remain = [s for s in sentences if s not in used_sentences]
                if remain:
                    fact_points.append(f"【核心论据】{trim(remain[0], 60)}")
                    used_sentences.add(remain[0])

        # 4. 提取主体机制与路径展开
        mid_points: List[str] = []
        mid_candidates = [
            s for s in sentences
            if s not in used_sentences and not is_question(s)
        ]
        for s in mid_candidates[:2]:
            mid_points.append(trim(s, 55))
            used_sentences.add(s)

        # 5. 提取趋势、启示与结论
        conclusion_points: List[str] = []
        concl_candidates = [
            s for s in sentences[-2:]
            if s not in used_sentences and not is_question(s)
        ]
        if concl_candidates:
            conclusion_points.append(trim(concl_candidates[-1], 55))
        else:
            conclusion_points.append(f"持续关注「{title}」在行业实践中的落地效能与边界约束")

        # 6. 动态生成思维导图
        mindmap_lines = [f"# {title}"]
        mindmap_lines.append("## 一、核心背景与问题提出")
        for p in intro_points[:2]:
            mindmap_lines.append(f"- {p}")

        mindmap_lines.append("## 二、核心事实论据与技术拆解")
        if fact_points:
            for p in fact_points[:3]:
                mindmap_lines.append(f"- {p}")
        else:
            mindmap_lines.append(f"- 围绕「{title}」梳理核心技术参数与指标依据")

        mindmap_lines.append("## 三、核心机制与实施路径")
        if mid_points:
            for p in mid_points[:2]:
                mindmap_lines.append(f"- {p}")
        else:
            mindmap_lines.append("- 核心实施路径设计与对比评估")
            mindmap_lines.append("- 方案可行性与边界条件推演")

        mindmap_lines.append("## 四、潜在挑战与未来趋势")
        for p in conclusion_points[:1]:
            mindmap_lines.append(f"- {p}")
        if claims:
            has_risk = any(c.get("confidence_level") in ("yellow", "red") for c in claims)
            if has_risk:
                mindmap_lines.append("- 【核验警示】部分断言缺乏充分权威信源背书，需保持审慎关注")
            else:
                mindmap_lines.append("- 【核查结论】核心数据与公开资料基本吻合，注意量产与落地约束")
        else:
            mindmap_lines.append("- 建议结合后续第三方复测与长期运行表现进行跟踪评估")

        mindmap = "\n".join(mindmap_lines)

        # 7. 动态生成结构化摘要
        summary_thesis = f"本文围绕「{title}」展开深度剖析。核心指出：{core_thesis}。"

        summary_items = []
        summary_items.append(f"1. 核心洞察：{trim(core_thesis, 100)}")

        if fact_points:
            joined_facts = "；".join([trim(fp, 80) for fp in fact_points[:2]])
            summary_items.append(f"2. 论据支撑：{joined_facts}")
        else:
            summary_items.append("2. 论据支撑：通过多维论述与关键段落推演提供事实支撑。")

        if mid_points:
            summary_items.append(f"3. 实施路径：{trim(mid_points[0], 100)}")
        else:
            summary_items.append("3. 实施路径：结合实际业务与工程场景，推进核心方案的渐进式落地。")

        summary_items.append(f"4. 价值结论：{trim(conclusion_points[0], 100)}")

        structured_summary = f"【核心论点】\n{summary_thesis}\n\n【脱水要点】\n" + "\n".join(summary_items)

        # 8. 动态生成最终长篇报告
        final_report = f"""# 📄 《{title}》智能脱水与深度核验报告

---

## 📌 核心思维导图
```markdown
{mindmap}
```

---

## 💡 深度脱水摘要
{structured_summary}

---

## 🛡️ 事实断言与动态核查结果
| 断言编号 | 断言原文 | 类型 | 核验评级 | 核查证据与结论 |
| :--- | :--- | :--- | :---: | :--- |
"""
        for c in claims:
            color_badge = {
                "green": "🟢 可信",
                "yellow": "🟡 存疑",
                "red": "🔴 违规/虚假",
                "outdated": "🕒 已过时",
                "unverified": "⚪ 免核验(常规观点)",
            }.get(c.get("confidence_level", "unverified"), "⚪ 未知")
            reason = c.get("verification_reason") or "常规观点表述，无需外部搜索引擎硬性验证。"
            final_report += f"| {c.get('claim_id')} | {c.get('text')[:40]}... | {c.get('claim_type')} | {color_badge} | {reason} |\n"

        final_report += "\n---\n\n## 🔍 批判性阅读建议\n"
        if claims:
            yellow_or_red = [c for c in claims if c.get("confidence_level") in ("yellow", "red")]
            outdated_claims = [c for c in claims if c.get("confidence_level") == "outdated"]
            if yellow_or_red:
                cids = ", ".join([c.get("claim_id", "") for c in yellow_or_red])
                final_report += f"- ⚠️ **关注存疑断言**：断言 [{cids}] 在公开交叉核查中存在争议或证据不足，建议进一步对比独立第三方权威评测报告。\n"
            if outdated_claims:
                cids = ", ".join([c.get("claim_id", "") for c in outdated_claims])
                final_report += f"- 🕒 **时效与演进关注**：断言 [{cids}] 属历史特定阶段事实，但当前行业数据已发生演化或刷新，请以最新披露为准。\n"
            if not yellow_or_red and not outdated_claims:
                final_report += "- ✅ **数据基准交叉核实**：文中硬性数据断言与行业公开发布资料基本吻合，可信度较高。\n"
        final_report += f"- 💡 **理性看待落地约束**：对文中提及的突破性指标（如相关提升幅度、量产时间表等），建议关注其工程落地与边界约束条件。\n"

        return {
            "mindmap": mindmap,
            "summary": structured_summary,
            "final_report": final_report,
        }
