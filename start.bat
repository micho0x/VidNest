@echo off
setlocal EnableExtensions
title VidNest
cd /d "%~dp0"
set "PF86=%ProgramFiles(x86)%"

call :find_python
if not defined PY goto :setup

:run
%PY% app.py
if errorlevel 1 (
    echo.
    echo   VidNest closed with an error. The text above says why.
    pause
)
exit /b 0

:setup
echo.
echo   Python was not found on this PC.
echo.
echo   VidNest needs Python 3.9 or newer. install.bat can
echo   fetch it for you, and Node.js too ^(recommended^).
echo.
choice /c YN /n /m "   Run install.bat now? [Y/N] "
if errorlevel 2 goto :manual

call "%~dp0install.bat" --from-start
call :find_python
if defined PY goto :run

:manual
echo.
echo   Install Python from https://www.python.org/downloads/windows/
echo   and tick "Add python.exe to PATH", then run start.bat again.
echo.
pause
exit /b 1

rem Finds a working Python and stores the command in PY.
:find_python
set "PY="

where python >nul 2>&1
if not errorlevel 1 (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if defined PY exit /b 0

where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)
if defined PY exit /b 0

for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if not defined PY if exist "%%~fD\python.exe" set "PY=%%~fD\python.exe"
)
if defined PY exit /b 0

for /d %%D in ("%ProgramFiles%\Python3*") do (
    if not defined PY if exist "%%~fD\python.exe" set "PY=%%~fD\python.exe"
)
if defined PY exit /b 0

if defined PF86 for /d %%D in ("%PF86%\Python3*") do (
    if not defined PY if exist "%%~fD\python.exe" set "PY=%%~fD\python.exe"
)
exit /b 0
