@echo off
setlocal
cd /d "%~dp0"
title Prisma 多软件自动化测试平台

set "APP=%~dp0Prisma.exe"
if not exist "%APP%" set "APP=%~dp0relap5-gui.exe"
if not exist "%APP%" set "APP=%~dp0tauri-app\src-tauri\target\release\Prisma.exe"
if not exist "%APP%" set "APP=%~dp0tauri-app\src-tauri\target\release\relap5-gui.exe"
if not exist "%APP%" (
    echo 未找到 Prisma.exe，请确认压缩包已完整解压。
    pause
    exit /b 1
)

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    where python >nul 2>nul
    if not errorlevel 1 goto create_venv
    where py >nul 2>nul
    if errorlevel 1 goto python_missing
    set "PYTHON_CMD=py -3"
    goto create_venv

:python_missing
    echo 未找到 Python 3。请先安装 Python 3.11 或更高版本，并勾选 Add Python to PATH。
    pause
    exit /b 1

:create_venv
    if not defined PYTHON_CMD set "PYTHON_CMD=python"
    echo 首次运行：正在创建 Python 环境...
    %PYTHON_CMD% -m venv "%~dp0.venv"
    if errorlevel 1 (
        echo 创建 Python 环境失败。
        pause
        exit /b 1
    )
)

"%PYTHON%" -c "import openpyxl, yaml" >nul 2>nul
if errorlevel 1 (
    echo 正在安装运行依赖...
    "%PYTHON%" -m pip install --disable-pip-version-check -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo 安装依赖失败，请检查网络后重试。
        pause
        exit /b 1
    )
)

start "" "%APP%"
endlocal
