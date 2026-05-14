@echo off
echo ============================================================
echo   🔧 SETUP INICIAL - DARK CHANNEL FACTORY
echo ============================================================
echo.

echo [1/3] Aplicando migracao do banco de dados...
python migrate_db.py
if %errorlevel% neq 0 (
    echo ❌ Falha na migracao! Verifique DATABASE_URL no .env
    pause
    exit /b 1
)
echo.

echo [2/3] Verificando FFmpeg...
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo ⚠️  FFmpeg nao encontrado. Instalando via winget...
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    if %errorlevel% neq 0 (
        echo ❌ Falha ao instalar FFmpeg automaticamente.
        echo    Instale manualmente: https://ffmpeg.org/download.html
        echo    Ou execute: winget install Gyan.FFmpeg
        pause
    ) else (
        echo ✅ FFmpeg instalado! Reinicie o terminal para que o PATH seja atualizado.
    )
) else (
    echo ✅ FFmpeg ja instalado.
)
echo.

echo [3/3] Limpando log antigo (factory_console.log)...
if exist factory_console.log (
    rem Use python truncation instead of ren -- works even when factory_service.py has the file open
    python -c "open('factory_console.log', 'w').close(); print('[OK] factory_console.log truncado com sucesso.')"
) else (
    echo    factory_console.log nao encontrado, nada a fazer.
)
echo.

echo ============================================================
echo [4/4] Executando Health Check final...
echo ============================================================
python health_check.py

pause
