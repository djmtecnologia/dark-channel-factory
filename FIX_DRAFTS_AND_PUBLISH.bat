@echo off
chcp 65001 > nul
title Fix Drafts and Publish - Dark Channel Factory
cd /d "%~dp0"
echo.
echo ================================================
echo  FIX DRAFTS AND PUBLISH - Dark Channel Factory
echo  Gera thumbs + Edita rascunhos + Agenda videos
echo ================================================
echo.
python fix_drafts_and_publish.py
pause
