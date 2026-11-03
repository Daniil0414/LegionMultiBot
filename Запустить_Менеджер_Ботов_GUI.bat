@echo off
chcp 65001 >nul
title Legion Multi-Bot GUI
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel% equ 0 (
    start "" pythonw gui_launcher.py
) else (
    start "" python gui_launcher.py
)
exit
