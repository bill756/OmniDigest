import logging
import asyncio
from typing import List, Dict, Any, Tuple, Optional
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def get_clean_base_url(url: str) -> str:
    """清理 base_url，自动剔除 /v1/systemone 或 /v1 后缀以适配 typesafe-sdk"""
    u = url.strip().rstrip("/")
    if u.endswith("/v1/systemone"):
        u = u[:-13].rstrip("/")
    elif u.endswith("/v1"):
        u = u[:-3].rstrip("/")
    return u or "https://api.typesafe.ai"


def is_typesafe_enabled() -> bool:
    """检查 TypeSafe AI Jev 官方接口是否已启用"""
    return bool(settings.TYPESAFE_API_KEY and settings.TYPESAFE_API_KEY.strip())


async def evaluate_and_filter_evidences(
    claim_text: str,
    evidences: List[Dict[str, Any]],
    auto_accept_threshold: float = 0.75,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """使用 TypeSafe AI Jev (System One) 官方模型对候选证据进行批量相关性质检与噪声过滤：
    
    1. 采用 Choice 原语判断每个证据片段与断言的语义关系（supports / contradicts / says_nothing）；
    2. 高置信度 (>= threshold) 判定为 says_nothing 的泛百科或无关背景被精准剔除；
    3. 若全部证据均被判定为 says_nothing，触发快速熔断 (fast_path)，免调用 DeepSeek 直接结案；
    4. 过滤后保留支持或反驳断言的真正相关证据，传递给 DeepSeek 进行自然语言推理。
    """
    meta_result: Dict[str, Any] = {
        "jev_enabled": False,
        "fast_path": False,
        "total_candidates": len(evidences),
        "kept_count": len(evidences),
        "filtered_count": 0,
        "details": [],
    }

    if not is_typesafe_enabled() or not evidences:
        return evidences, meta_result

    meta_result["jev_enabled"] = True

    try:
        from typesafe_sdk import AsyncTypeSafeClient, Choice

        base_url = get_clean_base_url(settings.TYPESAFE_ENDPOINT)

        # 构建批量质检 state 与 Choice questions
        state: Dict[str, Any] = {"claim": claim_text}
        questions: Dict[str, Any] = {}

        criteria = {
            "supports": "The evidence directly confirms the claim or provides positive factual proof",
            "contradicts": "The evidence directly refutes the claim or proves it false",
            "says_nothing": "The evidence does not address the specific claim or is general/unrelated background/profile",
        }

        for idx, item in enumerate(evidences):
            key = f"evidence_{idx}"
            snippet = f"{item.get('title', '')} {item.get('snippet', '')}".strip()
            state[key] = snippet
            questions[key] = Choice(
                instructions=f"How does `{key}` relate to `claim`?",
                criteria=criteria,
            )

        async with AsyncTypeSafeClient(
            api_key=settings.TYPESAFE_API_KEY,
            base_url=base_url,
            timeout=15.0,
        ) as client:
            resp = None
            for attempt in range(3):
                try:
                    resp = await client.system_one(
                        state=state,
                        questions=questions,
                        model=settings.TYPESAFE_MODEL,
                    )
                    break
                except Exception as req_err:
                    if attempt < 2 and any(err_code in str(req_err) for err_code in ["503", "529", "timeout", "timed out"]):
                        await asyncio.sleep(1.5)
                        continue
                    raise req_err

        kept_evidences: List[Dict[str, Any]] = []
        answers = resp.answers

        for idx, item in enumerate(evidences):
            key = f"evidence_{idx}"
            ans = answers.get(key)
            if not ans:
                kept_evidences.append(item)
                continue

            choice = ans.choice
            conf = float(ans.confidence or 0.0)
            meta_detail = {
                "url": item.get("url"),
                "title": item.get("title"),
                "choice": choice,
                "confidence": conf,
            }
            meta_result["details"].append(meta_detail)

            # 过滤逻辑：高置信度的 says_nothing 作为纯噪音丢弃
            if choice == "says_nothing" and conf >= auto_accept_threshold:
                continue

            # 挂载 Jev 的质检标签
            item["jev_relation"] = choice
            item["jev_confidence"] = conf
            kept_evidences.append(item)

        meta_result["kept_count"] = len(kept_evidences)
        meta_result["filtered_count"] = len(evidences) - len(kept_evidences)

        # 若全部候选证据被确凿判定为无关噪音，触发快速熔断
        if not kept_evidences and evidences:
            meta_result["fast_path"] = True
            meta_result["fast_path_reason"] = "经过 TypeSafe Jev 严苛质检，全网召回的候选证据均为无关背景或百科噪声，未包含断言所指具体案情事实。"

        return kept_evidences, meta_result

    except Exception as e:
        logger.warning(f"TypeSafe Jev 质检异常，透明降级至默认链路: {e}")
        return evidences, meta_result
