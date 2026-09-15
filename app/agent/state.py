from typing import List, Dict, Any, Optional
from typing_extensions import TypedDict


class DigestState(TypedDict, total=False):
    # 输入字段
    url: str
    title: str
    author: Optional[str]
    publish_date: Optional[str]
    platform: str
    clean_content: str
    token_count: int
    enable_fact_check: bool
    content_chunks: List[str]

    # Node A 抽取输出
    claims: List[Dict[str, Any]]
    need_fact_check: bool

    # Node C 搜索与核验输出
    evidences: Dict[str, List[Dict[str, str]]]
    fact_check_results: List[Dict[str, Any]]

    # Node D 报告合成输出
    mindmap: str
    summary: str
    final_report: str

    # 执行事件与日志追踪
    events: List[Dict[str, Any]]
    error: Optional[str]
