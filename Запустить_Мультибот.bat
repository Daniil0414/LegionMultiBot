@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Legion Multi-Bot Client

if exist "%~dp0app\LegionMultiBot.exe" (
    start "" "%~dp0app\LegionMultiBot.exe"
    exit /b
)

where pythonw >nul 2>nul
if %errorlevel% equ 0 (
    start "" pythonw app_container.py
) else (
    start "" python app_container.py
)
exit
