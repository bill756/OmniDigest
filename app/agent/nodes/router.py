from typing import Literal
from app.agent.state import DigestState


def route_decision(state: DigestState) -> Literal["fact_check", "synthesize"]:
    """Node B: 条件路由判定 - 存疑硬事实走向核验，纯观点直接合成报告"""
    need_check = state.get("need_fact_check", False)
    if need_check:
        return "fact_check"
    return "synthesize"
