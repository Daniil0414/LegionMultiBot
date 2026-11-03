@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Legion Multi-Bot Client

if not exist "%~dp0app\LegionMultiBot.exe" (
    echo Не найдено приложение. Распакуйте полностью LegionMultiBot-windows.zip из раздела Releases.
    pause
    exit /b 1
)
start "" "%~dp0app\LegionMultiBot.exe"
exit
