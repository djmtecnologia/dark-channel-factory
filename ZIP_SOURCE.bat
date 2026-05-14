@echo off
echo [ZIP] Criando arquivo de codigo fonte...
python create_zip.py
if %ERRORLEVEL% EQU 0 (
    echo.
    echo ✅ Sucesso! O arquivo 'dark-channel-factory-source.zip' foi criado.
) else (
    echo.
    echo ❌ Erro ao criar o arquivo ZIP. Verifique se o Python esta instalado.
)
pause
