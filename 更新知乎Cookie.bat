@echo off
chcp 65001 >nul
title OmniDigest - 自动同步知乎Cookie
cd /d "%~dp0"

echo 正在使用当前虚拟环境 (.venv) 同步知乎 Cookie...
.\.venv\Scripts\python.exe scripts\sync_zhihu_cookie.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo 执行出现异常，错误代码：%ERRORLEVEL%
    pause
)
