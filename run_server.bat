@echo off
chcp 65001 >nul
title سيرفر البيت الذكي - Smart Home Server

echo ========================================================
echo        🏠 تشغيل سيرفر البيت الذكي ونفق كلاود فلير
echo ========================================================
echo.

set UV_PATH=%USERPROFILE%\.local\bin\uv.exe

if exist "%~dp0cloudflared.exe" (
    echo [1/3] جاري تشغيل نفق كلاود فلير العالمي...
    start /b "" "%~dp0cloudflared.exe" tunnel --url http://localhost:5000
)

echo [2/3] فتح المتصفح...
start http://localhost:5000

echo [3/3] تشغيل السيرفر...
if exist "%UV_PATH%" (
    "%UV_PATH%" run --with flask --with pillow --with pymupdf python "%~dp0home_server.py"
) else (
    python "%~dp0home_server.py"
)

if errorlevel 1 (
    echo.
    echo [!] حدث خطأ أثناء تشغيل السيرفر.
    pause
)
