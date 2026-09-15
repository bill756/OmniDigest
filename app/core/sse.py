import json
from typing import Any, AsyncGenerator, Dict, Union, Optional


def format_sse(
    data: Union[str, Dict[str, Any], list],
    event: Optional[str] = None,
    event_id: Optional[str] = None,
    retry: Optional[int] = None,
) -> str:
    """按 SSE 标准规范格式化数据包"""
    lines = []
    if event:
        lines.append(f"event: {event}")
    if event_id:
        lines.append(f"id: {event_id}")
    if retry is not None:
        lines.append(f"retry: {retry}")

    if isinstance(data, (dict, list)):
        payload = json.dumps(data, ensure_ascii=False)
    else:
        payload = str(data)

    # 规范：多行数据每一行都需以 data: 开头
    for line in payload.splitlines():
        lines.append(f"data: {line}")
    lines.append("\n")
    return "\n".join(lines)



