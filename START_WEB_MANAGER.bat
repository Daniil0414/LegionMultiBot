@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Legion Multi-Bot Profile Manager
node server.js --open
if errorlevel 1 pause
