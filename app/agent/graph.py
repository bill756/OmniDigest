from typing import AsyncGenerator, Dict, Any
from langgraph.graph import StateGraph, END
from app.agent.state import DigestState
from app.agent.nodes.claim_extract import claim_extract_node
from app.agent.nodes.router import route_decision
from app.agent.nodes.fact_check import fact_check_node
from app.agent.nodes.synthesize import synthesize_node


def build_digest_graph():
    """构建智能体状态图编排"""
    workflow = StateGraph(DigestState)

    # 注册节点
    workflow.add_node("claim_extract", claim_extract_node)
    workflow.add_node("fact_check", fact_check_node)
    workflow.add_node("synthesize", synthesize_node)

    # 设置起点
    workflow.set_entry_point("claim_extract")

    # 条件分支路由：Node A -> Node B(路由) -> Node C 或 Node D
    workflow.add_conditional_edges(
        "claim_extract",
        route_decision,
        {
            "fact_check": "fact_check",
            "synthesize": "synthesize",
        },
    )

    # 核验完毕反哺进入报告合成
    workflow.add_edge("fact_check", "synthesize")

    # 合成完毕结束
    workflow.add_edge("synthesize", END)

    return workflow.compile()


digest_graph = build_digest_graph()


async def stream_agent_events(initial_state: DigestState) -> AsyncGenerator[Dict[str, Any], None]:
    """通过 astream 实时流式捕获各节点的阶段执行事件"""
    yield {
        "stage": "agent_start",
        "message": "启动智能体工作流 (StateGraph)...",
    }

    current_state = dict(initial_state)
    async for output in digest_graph.astream(initial_state):
        for node_name, node_output in output.items():
            current_state.update(node_output)
            yield {
                "stage": node_name,
                "node": node_name,
                "data": node_output,
            }

    yield {
        "stage": "agent_complete",
        "final_state": current_state,
    }
