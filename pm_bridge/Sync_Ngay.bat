@echo off
chcp 65001 > nul
title PM Assistant - Đồng bộ dữ liệu dự án
echo ========================================================
echo        PM ASSISTANT BRIDGE - SYNC NGAY LẬP TỨC
echo ========================================================
echo.

py -3.11 "%~dp0main.py"

echo.
echo ========================================================
echo Quá trình đồng bộ hoàn tất. Nhấn phím bất kỳ để đóng...
pause > nul
