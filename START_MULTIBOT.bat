@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Legion Multi-Bot Client

where pythonw >nul 2>nul
if %errorlevel% equ 0 (
    start "" pythonw app_container.py
) else (
    start "" python app_container.py
)
exit
