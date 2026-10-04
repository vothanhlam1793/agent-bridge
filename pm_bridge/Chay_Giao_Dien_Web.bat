@echo off
chcp 65001 > nul
title PM Assistant Bridge - Khởi chạy Giao diện
echo ==================================================================
echo        ĐANG KHỞI ĐỘNG GIAO DIỆN PM ASSISTANT BRIDGE...
echo ==================================================================
echo.

py -3.11 "%~dp0web_app.py"

pause
