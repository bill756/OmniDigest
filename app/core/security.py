import ipaddress
import re
from typing import Optional
from urllib.parse import urlparse
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader
from app.config import get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: Optional[str] = Security(api_key_header)) -> bool:
    settings = get_settings()
    if not settings.API_KEY:
        # 未配置 API_KEY 时，默认为开放开发模式
        return True
    if api_key != settings.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 API 鉴权密钥 (Invalid API Key)",
        )
    return True


def validate_url(url: str) -> str:
    """验证并清洗待解析的 URL，防止 SSRF 及畸形输入"""
    if not url or not isinstance(url, str):
        raise HTTPException(status_code=400, detail="URL 不能为空")

    url = url.strip()
    try:
        parsed = urlparse(url)
    except Exception:
        raise HTTPException(status_code=400, detail="无法解析给定的 URL 格式")

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400,
            detail=f"不支持的协议: {parsed.scheme}，仅支持 http 或 https",
        )

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="URL 必须包含有效的主机名")

    # SSRF 防御：阻止访问本地回环与常见局域网 IP
    blocked_hostnames = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
    if hostname.lower() in blocked_hostnames:
        raise HTTPException(status_code=400, detail="禁止访问本地回环地址")

    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
            raise HTTPException(status_code=400, detail="禁止访问内网保留 IP 地址")
    except ValueError:
        # hostname 为常规域名，通过
        pass

    return url
