@echo off
setlocal
cd /d "%~dp0"

rem Prefer the project virtual environment if it exists
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

echo ================================================
echo   Prisma Auto Test Framework
echo ================================================
echo.
%PYTHON% -m run_prisma
echo.
echo ================================================
echo   Done. Results: data\test_check.xlsx
echo ================================================
pause