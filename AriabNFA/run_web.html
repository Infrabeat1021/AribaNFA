@echo off
REM Launch the browser interface. Serves on this machine only.
REM
REM Every failure path pauses and prints the folder it looked in. Without that,
REM the window closes instantly and the user cannot tell whether setup failed,
REM ran somewhere else, or never ran at all.

setlocal
set "PROJ=%~dp0"
set "VENVPY=%PROJ%.venv\Scripts\python.exe"

if exist "%VENVPY%" goto :launch

echo.
echo ===============================================================
echo   The app is not set up in this folder yet.
echo ===============================================================
echo.
echo   This folder:
echo     %PROJ%
echo.
echo   Looked for:
echo     %VENVPY%
echo.

if not exist "%PROJ%setup.bat" (
    echo   setup.bat is not here either, so this is not the app folder.
    echo   Find the folder containing setup.bat and run it from there.
    echo.
    pause
    exit /b 1
)

echo   What is in this folder:
for %%f in ("%PROJ%*.bat") do echo     %%~nxf
if exist "%PROJ%.venv" (
    echo.
    echo   A .venv folder exists but has no working Python inside it,
    echo   so setup did not finish. Run setup.bat again and read the
    echo   last few lines before it closes.
) else (
    echo.
    echo   There is no .venv folder, so setup.bat has not completed here.
    echo.
    echo   If you ran setup.bat by double-clicking it inside the ZIP file,
    echo   it installed into a temporary folder that Windows has discarded.
    echo   Extract the ZIP to a real folder first - for example
    echo   C:\Users\%USERNAME%\AriabNFA - then run setup.bat from there.
)
echo.
pause
exit /b 1

:launch
echo Starting the InfraBeat NFA Generator...
echo.
"%VENVPY%" -m ariabnfa --web %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo ---------------------------------------------------------------
    echo  It stopped with an error ^(code %RC%^).
    echo.
    echo  Common causes:
    echo    * Another copy is already running - look for a console window,
    echo      or open http://127.0.0.1:5000/ in your browser.
    echo    * Setup did not finish - run setup.bat again and read the end.
    echo.
    echo  The log is at:
    echo    %LOCALAPPDATA%\AriabNFA\logs\ariabnfa.log
    echo ---------------------------------------------------------------
    echo.
    pause
    exit /b %RC%
)

endlocal
