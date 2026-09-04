@echo off
REM Development launcher: keeps the console open so tracebacks are visible.
setlocal
set "PROJ=%~dp0"
"%PROJ%.venv\Scripts\python.exe" -m ariabnfa --verbose %*
pause
endlocal
