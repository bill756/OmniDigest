import ctypes
import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 模块搜索路径
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.crawler.cookie_sync import (
    auto_sync_zhihu_cookie,
    fetch_cookies_via_cdp,
    fetch_cookies_via_rookiepy,
    update_env_file,
)


def is_admin() -> bool:
    """检查当前进程是否具有 Windows 管理员权限"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_as_admin():
    """通过 Windows ShellExecute 触发 UAC 弹窗提权重新执行本脚本"""
    params = f'"{Path(__file__).resolve()}" --elevated'
    # 优先使用当前正在运行的 Python 解释器（即 .venv 中的 python.exe）
    executable = sys.executable
    ret = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        executable,
        params,
        str(project_root),
        1  # SW_SHOWNORMAL
    )
    return ret > 32


def main():
    print("=" * 60)
    print("        OmniDigest - 知乎 Cookie 本地自动同步工具        ")
    print("=" * 60)

    # 1. 优先尝试免权限的 CDP 调试端口
    print("[1/2] 正在检测 Chrome 远程调试端口 (CDP)...")
    cdp_cookie = fetch_cookies_via_cdp()
    if cdp_cookie:
        env_path = str(project_root / ".env")
        update_env_file(cdp_cookie, env_path=env_path)
        print("\n[SUCCESS] 成功通过 Chrome 远程调试端口 (CDP) 提取最新 Cookie！")
        print(f"[SUCCESS] 已自动更新至: {env_path}")
        print("=" * 60)
        if "--elevated" in sys.argv:
            input("\n按回车键退出窗口...")
        return

    # 2. 尝试从本地 Chrome/Edge 浏览器数据库解密
    print("[2/2] 正在从本地 Chrome/Edge 浏览器数据库读取知乎凭据...")
    cookie_str, err = fetch_cookies_via_rookiepy()

    if cookie_str:
        env_path = str(project_root / ".env")
        update_env_file(cookie_str, env_path=env_path)
        print("\n[SUCCESS] 成功从本地浏览器提取并解密知乎 Cookie！")
        print(f"[SUCCESS] 已自动安全更新至: {env_path}")
        # 显示部分关键字段用于核对
        has_zc0 = "z_c0=" in cookie_str
        has_dc0 = "d_c0=" in cookie_str
        has_zse = "__zse_ck=" in cookie_str
        print(f"[STATUS] 凭证完整度: z_c0 (登录凭据): {has_zc0} | d_c0 (设备指纹): {has_dc0} | __zse_ck: {has_zse}")
        print("=" * 60)
        if "--elevated" in sys.argv:
            input("\n按回车键退出窗口...")
        return

    # 3. 处理 Chrome 130+ App-Bound Encryption
    if err and "appbound encryption" in err.lower():
        if not is_admin():
            print("\n[NOTICE] Chrome/Edge 130+ 启用了系统级 App-Bound Encryption 保护。")
            if "--no-prompt" in sys.argv:
                print("[INFO] (--no-prompt 模式) 请以管理员权限运行此脚本，或双击「更新知乎Cookie.bat」授权完成同步。")
                return
            print("[NOTICE] 正在为您唤起 Windows UAC 授权弹窗（点击「是」即可自动完成解密与写入）...")
            elevated = run_as_admin()
            if elevated:
                print("[INFO] 已拉起管理员窗口进行同步，本窗口即将退出。")
                return
            else:
                print("\n[ERROR] 用户取消了管理员授权。")
        else:
            print(f"\n[ERROR] 在管理员权限下依然无法解密 Cookie: {err}")
    else:
        print(f"\n[ERROR] 提取 Cookie 失败: {err or '未找到有效 Cookie'}")

    print("=" * 60)
    if "--elevated" in sys.argv:
        input("\n按回车键退出窗口...")


if __name__ == "__main__":
    main()
