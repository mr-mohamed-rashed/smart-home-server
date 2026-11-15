@echo off
chcp 65001 >nul
title سيرفر البيت الذكي - Smart Home Server

echo ========================================================
echo        🏠 تشغيل سيرفر البيت الذكي للطباعة والمشاركة
echo ========================================================
echo.

set UV_PATH=%USERPROFILE%\.local\bin\uv.exe

if exist "%UV_PATH%" (
    echo [1/2] جاري تجهيز بيئة التشغيل السريعة ودعم الصور و PDF...
    start http://localhost:5000
    "%UV_PATH%" run --with flask --with pillow --with pymupdf python "%~dp0home_server.py"
) else (
    echo [1/2] جاري فحص بايثون...
    start http://localhost:5000
    python "%~dp0home_server.py"
)

if errorlevel 1 (
    echo.
    echo [!] حدث خطأ أثناء تشغيل السيرفر.
    pause
)
