@echo off
chcp 65001 > nul
title 🎨 Regenerador de Thumbnails — Dark Channel Factory
cd /d "%~dp0"
echo.
echo  ╔═══════════════════════════════════════════╗
echo  ║   🎨 REGENERADOR DE THUMBNAILS            ║
echo  ╚═══════════════════════════════════════════╝
echo.
python regenerar_thumbs.py
pause
