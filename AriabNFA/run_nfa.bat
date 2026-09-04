@echo off
REM Launch the NFA generator. pythonw.exe runs without a console window.
setlocal
set "PROJ=%~dp0"
if not exist "%PROJ%.venv\Scripts\pythonw.exe" (
    echo Environment not set up yet. Run setup.bat first.
    pause
    exit /b 1
)
start "" "%PROJ%.venv\Scripts\pythonw.exe" -m ariabnfa
endlocal
