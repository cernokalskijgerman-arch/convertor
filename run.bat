@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set PYCMD=
where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set PYCMD=py
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        set PYCMD=python
    )
)

if "%PYCMD%"=="" (
    echo [!] Error: Python not found!
    pause
    exit /b 1
)

%PYCMD% "%~dp0ascii_photo.py" %*

pause
