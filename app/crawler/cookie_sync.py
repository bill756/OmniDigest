import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

from app.config import update_zhihu_cookie

logger = logging.getLogger(__name__)

# 知乎反爬与身份认证的关键 Cookie 键名（供校验完整度）
CRITICAL_ZHIHU_COOKIE_KEYS = {"d_c0", "z_c0", "_xsrf"}


def format_cookie_list_to_string(cookies: List[Dict]) -> str:
    """将 Cookie 字典列表转换为标准的 Cookie 请求头字符串格式"""
    cookie_pairs = []
    seen_names = set()
    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        if name and value and name not in seen_names:
            seen_names.add(name)
            cookie_pairs.append(f"{name}={value}")
    return "; ".join(cookie_pairs)


def update_env_file(new_cookie: str, env_path: Optional[str] = None) -> bool:
    """安全、原子化地更新 .env 文件中的 ZHIHU_COOKIE 字段，保留其他所有配置"""
    if env_path is None:
        # 寻找项目根目录下的 .env
        current_dir = Path(__file__).resolve().parent
        while current_dir.parent != current_dir:
            candidate = current_dir / ".env"
            if candidate.exists():
                env_path = str(candidate)
                break
            current_dir = current_dir.parent
        if not env_path:
            env_path = ".env"

    env_file = Path(env_path)
    if not env_file.exists():
        logger.warning(f".env 文件不存在: {env_path}，将直接创建新文件")
        env_file.write_text(f'ZHIHU_COOKIE="{new_cookie}"\n', encoding="utf-8")
        update_zhihu_cookie(new_cookie)
        return True

    try:
        content = env_file.read_text(encoding="utf-8")
        pattern = r'^(ZHIHU_COOKIE\s*=\s*).*$'
        replacement = f'ZHIHU_COOKIE="{new_cookie}"'

        if re.search(pattern, content, flags=re.MULTILINE):
            new_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
        else:
            # 文件中不存在该配置，追加到末尾
            new_content = content.rstrip() + f'\n\n# 知乎爬虫反爬 Cookie (自动同步)\n{replacement}\n'

        env_file.write_text(new_content, encoding="utf-8")
        update_zhihu_cookie(new_cookie)
        logger.info(f"成功将最新知乎 Cookie 写回 .env ({env_path}) 并热重载至内存！")
        return True
    except Exception as e:
        logger.error(f"写入 .env 失败: {e}", exc_info=True)
        return False


def fetch_cookies_via_cdp(port: int = 9222) -> Optional[str]:
    """尝试通过 Chrome 远程调试端口 (CDP) 免权限提取 Cookie。
    若用户启动 Chrome 时指定了 --remote-debugging-port=9222，可直接免密读取。
    """
    try:
        import websockets.sync.client as ws_sync
    except ImportError:
        ws_sync = None

    url = f"http://127.0.0.1:{port}/json/version"
    try:
        with httpx.Client(timeout=1.5) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return None
            data = resp.json()
            ws_url = data.get("webSocketDebuggerUrl")
            if not ws_url or not ws_sync:
                return None

        # 通过 WebSocket 发送 CDP Network.getCookies
        with ws_sync.connect(ws_url) as ws:
            req = {
                "id": 1,
                "method": "Network.getCookies",
                "params": {"urls": ["https://www.zhihu.com", "https://zhuanlan.zhihu.com"]}
            }
            ws.send(json.dumps(req))
            result = json.loads(ws.recv())
            raw_cookies = result.get("result", {}).get("cookies", [])
            if raw_cookies:
                cookie_str = format_cookie_list_to_string(raw_cookies)
                return cookie_str
    except Exception:
        # CDP 端口未开放或未连接属于正常预期
        pass
    return None


def fetch_cookies_via_rookiepy() -> Tuple[Optional[str], Optional[str]]:
    """使用 rookiepy 从本地已安装的浏览器中提取知乎 Cookie。
    返回: (cookie_string, error_message)
    """
    try:
        import rookiepy
    except ImportError:
        return None, "未安装 rookiepy，请在虚拟环境中执行 pip install rookiepy"

    domains = [".zhihu.com", "zhihu.com"]
    browsers = [
        ("Chrome", rookiepy.chrome),
        ("Edge", rookiepy.edge),
        ("Firefox", rookiepy.firefox),
        ("Brave", rookiepy.brave),
    ]

    last_error = None
    appbound_error = None
    for name, fetch_fn in browsers:
        try:
            cookies = fetch_fn(domains=domains)
            if cookies:
                cookie_str = format_cookie_list_to_string(cookies)
                # 校验核心字段
                has_dc0 = "d_c0=" in cookie_str
                has_zc0 = "z_c0=" in cookie_str
                if has_dc0 or has_zc0:
                    logger.info(f"成功从本地 {name} 提取到知乎 Cookie (包含 d_c0: {has_dc0}, z_c0: {has_zc0})")
                    return cookie_str, None
        except Exception as e:
            err_msg = str(e)
            if "appbound encryption" in err_msg.lower():
                appbound_error = f"{name}: {err_msg}"
            last_error = f"{name}: {err_msg}"
            continue

    return None, (appbound_error or last_error)


def auto_sync_zhihu_cookie(env_path: Optional[str] = None) -> Tuple[bool, str]:
    """一键综合同步知乎 Cookie 入口：
    1. 优先尝试 CDP 远程调试端口无感免权限提取
    2. 尝试 rookiepy 本地数据库解密提取
    3. 成功后自动回写 .env 并热重载内存
    """
    # 1. 尝试 CDP
    cdp_cookie = fetch_cookies_via_cdp()
    if cdp_cookie:
        update_env_file(cdp_cookie, env_path=env_path)
        return True, "成功通过 Chrome 远程调试端口 (CDP) 提取并同步知乎最新 Cookie！"

    # 2. 尝试本地浏览器
    cookie_str, err = fetch_cookies_via_rookiepy()
    if cookie_str:
        update_env_file(cookie_str, env_path=env_path)
        return True, "成功从本地浏览器提取并同步知乎最新 Cookie！"

    # 3. 若触发 Windows 11 / Chrome 130+ App-Bound Encryption
    if err and "appbound encryption" in err.lower():
        msg = (
            "检测到 Chrome/Edge 启用了 App-Bound Encryption 保护。"
            "需要以管理员身份运行一次同步脚本 (scripts/sync_zhihu_cookie.py) 即可完成解密回写。"
        )
        return False, msg

    return False, f"未能从本地浏览器同步知乎 Cookie: {err or '未找到知乎登录凭据'}"
