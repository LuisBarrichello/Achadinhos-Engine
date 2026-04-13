@echo off
:: ============================================================
::  Achadinhos do Momento — build.bat
::  Empacota gui_organizer.py em um .exe standalone via PyInstaller
::
::  PRÉ-REQUISITOS:
::    1. Python 3.10+ instalado e no PATH
::    2. pip install pyinstaller customtkinter python-dotenv
::
::  COMO USAR:
::    Coloque este arquivo dentro da pasta video-organizer/
::    Dê dois cliques nele (ou execute no terminal: build.bat)
::
::  SAÍDA:
::    dist\VideoOrganizer.exe   ← executável final para o usuário
:: ============================================================

setlocal enabledelayedexpansion

:: ── Cores no terminal (Windows 10+) ──────────────────────────
for /f %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "GREEN=%ESC%[32m"
set "YELLOW=%ESC%[33m"
set "RED=%ESC%[31m"
set "RESET=%ESC%[0m"

echo.
echo %GREEN%============================================================%RESET%
echo %GREEN%  Achadinhos — Build do VideoOrganizer.exe%RESET%
echo %GREEN%============================================================%RESET%
echo.

:: ── Verifica Python ───────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo %RED%[ERRO] Python nao encontrado no PATH.%RESET%
    echo        Instale em https://python.org e marque "Add to PATH".
    pause
    exit /b 1
)

:: ── Instala dependências ──────────────────────────────────────
echo %YELLOW%[1/3] Instalando dependencias...%RESET%
pip install --quiet --upgrade pyinstaller customtkinter python-dotenv
if errorlevel 1 (
    echo %RED%[ERRO] Falha ao instalar dependencias.%RESET%
    pause
    exit /b 1
)

:: ── Limpa builds anteriores ───────────────────────────────────
echo %YELLOW%[2/3] Limpando builds anteriores...%RESET%
if exist "dist\VideoOrganizer.exe" del /q "dist\VideoOrganizer.exe"
if exist "build"                   rmdir /s /q "build"
if exist "VideoOrganizer.spec"     del /q "VideoOrganizer.spec"

:: ── PyInstaller ───────────────────────────────────────────────
:: Flags importantes:
::   --onefile       : tudo num único .exe (sem pasta de DLLs)
::   --windowed      : sem janela de console (modo GUI puro)
::   --name          : nome do executável final
::   --icon          : (opcional) ícone .ico — remova se não tiver
::   --collect-all   : garante que customtkinter empacote seus
::                     temas, fontes e assets corretamente
::   --hidden-import : importações dinâmicas que o PyInstaller
::                     não detecta sozinho

echo %YELLOW%[3/3] Compilando com PyInstaller...%RESET%
echo.

pyinstaller ^
    --onefile ^
    --windowed ^
    --name "VideoOrganizer" ^
    --collect-all customtkinter ^
    --hidden-import tkinter ^
    --hidden-import tkinter.filedialog ^
    --hidden-import tkinter.messagebox ^
    --hidden-import csv ^
    --hidden-import shutil ^
    --hidden-import unicodedata ^
    --hidden-import dotenv ^
    --hidden-import pathlib ^
    gui_organizer.py

:: ── Verifica saída ────────────────────────────────────────────
if exist "dist\VideoOrganizer.exe" (
    echo.
    echo %GREEN%============================================================%RESET%
    echo %GREEN%  SUCESSO! Executavel gerado em:%RESET%
    echo %GREEN%  %~dp0dist\VideoOrganizer.exe%RESET%
    echo %GREEN%============================================================%RESET%
    echo.
    echo  Copie o arquivo VideoOrganizer.exe para qualquer computador
    echo  com Windows — ele funciona sem instalar Python.
    echo.
    :: Abre a pasta dist no Explorer para facilitar
    explorer "dist"
) else (
    echo.
    echo %RED%[ERRO] Build falhou. Verifique as mensagens acima.%RESET%
    echo.
    echo  Dicas de solucao:
    echo   1. Tente rodar: pip install --upgrade pyinstaller
    echo   2. Verifique se ha erros de import no gui_organizer.py
    echo   3. Rode: python gui_organizer.py  para testar antes de compilar
    echo.
)

pause
endlocal
