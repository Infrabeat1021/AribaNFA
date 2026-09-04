@echo off
REM One-time setup: install Python if needed, create the virtual environment,
REM and install dependencies. Safe to run more than once.
REM
REM Interpreters are always invoked by absolute path, because the Microsoft
REM Store python.exe stub shadows PATH on these machines and does nothing.

setlocal
set "PROJ=%~dp0"
set "BASEPY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

echo ==========================================
echo   InfraBeat NFA Generator - first-time setup
echo ==========================================
echo.
echo Installing into:
echo   %PROJ%
echo.

REM Double-clicking setup.bat from inside the ZIP viewer makes Windows extract
REM to a temporary folder and run it there. Everything appears to succeed, then
REM the folder is discarded and run_web.bat finds no environment.
echo %PROJ% | findstr /I /C:"\\Temp\\" >nul
if not errorlevel 1 (
    echo ***************************************************************
    echo   WARNING: this looks like a temporary folder.
    echo.
    echo   If you double-clicked setup.bat inside the ZIP file, stop now:
    echo   Windows will throw this folder away and the app will not run.
    echo.
    echo   Extract the ZIP to a real folder first, for example
    echo     C:\Users\%USERNAME%\AriabNFA
    echo   then run setup.bat from there.
    echo ***************************************************************
    echo.
    pause
)

REM ---- 1. Python -----------------------------------------------------------
if exist "%BASEPY%" goto :havepython

echo Python 3.12 is not installed yet. Installing it for your user account
echo (no administrator rights needed). This takes a couple of minutes...
echo.
winget install --id Python.Python.3.12 --scope user --source winget ^
    --accept-package-agreements --accept-source-agreements --disable-interactivity
echo.

if exist "%BASEPY%" goto :havepython
echo ERROR: Python still not found at:
echo   %BASEPY%
echo.
echo If winget is unavailable, install Python 3.12 from python.org choosing
echo "Install for me only", then run this file again.
echo.
pause
exit /b 1

:havepython
for /f "tokens=*" %%v in ('"%BASEPY%" --version') do echo Using %%v
echo.

REM ---- 2. Virtual environment ---------------------------------------------
REM Reuse a working environment rather than rebuilding it. Recreating over an
REM existing one fails with "Permission denied" because Windows will not
REM overwrite python.exe in place, which broke re-running this file.
set "VENVPY=%PROJ%.venv\Scripts\python.exe"

if not exist "%VENVPY%" goto :makevenv
"%VENVPY%" -c "import sys" >nul 2>&1
if errorlevel 1 goto :makevenv
echo Reusing the existing environment.
goto :havevenv

:makevenv
echo Creating the virtual environment...
"%BASEPY%" -m venv "%PROJ%.venv"
if errorlevel 1 (
    echo.
    echo ERROR: Could not create the virtual environment.
    echo If the app is running, close it and try again.
    pause
    exit /b 1
)

:havevenv

REM ---- 3. Dependencies -----------------------------------------------------
REM Python 3.12 virtual environments no longer ship setuptools, and the
REM editable install below needs it to build the package metadata.
echo Preparing the environment...
"%VENVPY%" -m pip install --upgrade pip setuptools wheel --quiet

echo Installing dependencies...
"%VENVPY%" -m pip install -r "%PROJ%requirements-dev.txt" --quiet
if errorlevel 1 (
    echo.
    echo PyPI unreachable - trying the offline wheel bundle in vendor\wheels ...
    "%VENVPY%" -m pip install --no-index --find-links "%PROJ%vendor\wheels" -r "%PROJ%requirements-dev.txt"
    if errorlevel 1 (
        echo.
        echo ERROR: Could not install dependencies, online or offline.
        echo If you are behind a proxy, set HTTPS_PROXY and run this again.
        pause
        exit /b 1
    )
)

echo Installing the application...
"%VENVPY%" -m pip install -e "%PROJ%." --quiet --no-build-isolation
if errorlevel 1 (
    echo ERROR: Could not install the ariabnfa package.
    pause
    exit /b 1
)

REM ---- 4. Prove it actually works ------------------------------------------
REM "Setup complete" must mean the launcher will work, not merely that no step
REM reported an error along the way.
echo Verifying...
if not exist "%VENVPY%" goto :verifyfailed
"%VENVPY%" -m ariabnfa --version
if errorlevel 1 goto :verifyfailed

echo.
echo ==========================================
echo   Setup complete and verified.
echo ==========================================
echo.
echo   Installed in:
echo     %PROJ%
echo.
echo   Start the app:      run_web.bat        (opens in your browser)
echo   Desktop window:     run_nfa.bat
echo.
echo   First time in: open Settings and enter your Ariba credentials.
echo   See SETUP.md for what goes in each box.
echo.
pause
endlocal
exit /b 0

:verifyfailed
echo.
echo ==========================================
echo   Setup did NOT complete.
echo ==========================================
echo.
echo   Expected to find a working Python at:
echo     %VENVPY%
echo.
echo   Scroll up to see which step failed. If you are unsure, send the
echo   whole text of this window on.
echo.
pause
exit /b 1
