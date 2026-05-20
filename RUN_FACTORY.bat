@echo off
CLS
COLOR 0A
:start
echo ============================================================
echo   🚀 DARK CHANNEL FACTORY - PAINEL DE CONTROLE 24/7
echo ============================================================
echo.
echo [1] Iniciar SERVICO CENTRAL (Agendamento, Viralidade e 24/7)
echo [2] Iniciar WORKER DE EDICAO (Render e Upload)
echo [3] Limpar Logs e Temporarios
echo [4] Sair
echo [5] EXECUTAR TUDO (Servico + Worker)
echo [6] Diagnostico de Sistema (Health Check)
echo [7] Migrar Banco de Dados (Aplicar novas colunas)
echo [8] FORCAR NOVA PRODUCAO (Resetar jobs para PENDING)
echo [9] Resetar Jobs ZUMBIS (THINKING/DRAFTED presos)
echo [10] MODO ELITE 2026 (Multi-Canal US, FR, ES, DE)
echo [11] FORCAR MODO ELITE (Ignorar Cotas Diarias)
echo [12] MODO ELITE POR PAIS (Escolher Idiomas Especificos)
echo.
set /p opt="Escolha uma opcao: "

if "%opt%"=="1" goto op1
if "%opt%"=="2" goto op2
if "%opt%"=="3" goto op3
if "%opt%"=="4" goto op4
if "%opt%"=="5" goto op5
if "%opt%"=="6" goto op6
if "%opt%"=="7" goto op7
if "%opt%"=="8" goto op8
if "%opt%"=="9" goto op9
if "%opt%"=="10" goto op10
if "%opt%"=="11" goto op11
if "%opt%"=="12" goto op12

echo [ERRO] Opcao invalida.
pause
goto start

:op1
echo [SERVICO] Iniciando Orquestrador Global...
python factory_service.py
pause
goto start

:op2
echo [WORKER] Iniciando Motor de Render e Upload...
python desktop_node.py
pause
goto start

:op3
echo [LIMPEZA] Removendo arquivos temporarios e arquivando logs...
if exist factory_errors.log del /q factory_errors.log
if exist factory_console.log (
    for /f "tokens=1-3 delims=/ " %%a in ('date /t') do set DATESTAMP=%%a%%b%%c
    ren factory_console.log factory_console_%DATESTAMP%.log.bak
    echo factory_console.log arquivado como factory_console_%DATESTAMP%.log.bak
)
if exist output\audio rmdir /s /q output\audio
if exist output\videos\*\temp (
    for /d /r output\videos %%d in (temp) do if exist "%%d" rmdir /s /q "%%d"
)
echo Limpeza concluida.
pause
goto start

:op4
exit

:op5
echo [MODO IMPERIO] Iniciando Servico e Worker em janelas separadas...
start "SERVICO CENTRAL" cmd /k python factory_service.py
timeout /t 3 /nobreak >nul
start "WORKER EDICAO" cmd /k python desktop_node.py
echo Sistema total iniciado!
pause
goto start

:op6
echo [DIAGNOSTICO] Executando Health Check...
python health_check.py
pause
goto start

:op7
echo [MIGRATE] Aplicando migracoes de banco de dados...
python migrate_db.py
pause
goto start

:op8
echo [RESET] Forcando nova producao...
python force_videos.py
pause
goto start

:op9
echo [FIX] Resetando jobs ZUMBIS presos em THINKING/DRAFTED...
python fix_stuck_jobs.py
pause
goto start

:op10
echo [ELITE] Iniciando Producao Multi-Canal (US, FR, ES, DE)...
python elite_production_test.py
pause
goto start

:op11
echo [ELITE-FORCE] Forcando Producao Multi-Canal (Ignorando Cotas)...
python elite_production_test.py --force
pause
goto start

:op12
echo [ELITE-PAIS] Producao Especifica por Pais
echo Idiomas disponiveis: en-US, fr-FR, es-ES, de-DE
echo Exemplo para Espanha e Alemanha: es-ES,de-DE
set /p langs="Digite os idiomas (separados por virgula): "
python elite_production_test.py --force --langs "%langs%"
pause
goto start
