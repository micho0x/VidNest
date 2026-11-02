@echo off
setlocal EnableExtensions EnableDelayedExpansion
title VidNest - setup
cd /d "%~dp0"

set "PF86=%ProgramFiles(x86)%"
set "HERE=%~dp0"
set "PY_URL=https://www.python.org/ftp/python/3.13.7/python-3.13.7-amd64.exe"
set "NODE_URL=https://nodejs.org/dist/latest-v24.x/win-x64/node.exe"

echo.
echo   ==============================================
echo     VidNest - install what is missing
echo   ==============================================
echo.
echo   Python is required. Node.js is recommended.
echo   Windows may ask for permission - click Yes.
echo.

if /i not "%PROCESSOR_ARCHITECTURE%"=="AMD64" if /i not "%PROCESSOR_ARCHITEW6432%"=="AMD64" (
    echo   [!!]   VidNest needs 64-bit Windows 10 or 11.
    echo.
    pause
    exit /b 1
)

where winget >nul 2>&1
if errorlevel 1 (
    echo   [--]   winget not found - will download installers directly.
) else (
    echo   [ok]   winget is available.
)
echo.

call :ensure_python
call :ensure_node

echo.
echo   ==============================================
if defined MISSING_PY goto :not_ready

echo    All set. Double-click start.bat to run VidNest.
echo   ==============================================
echo.
if /i "%~1"=="--from-start" exit /b 0
choice /c YN /n /m "   Start VidNest now? [Y/N] "
if not errorlevel 2 start "" "%HERE%start.bat"
exit /b 0

:not_ready
echo    Python is still missing - see the notes above.
echo   ==============================================
echo.
if /i "%~1"=="--from-start" exit /b 1
pause
exit /b 1


rem ---------------------------------------------------------------- subroutines

:ensure_python
call :find_python
if defined PY (
    echo   [ok]   Python is installed  ^(%PY%^)
    exit /b 0
)

echo   [..]   Python was not found.
echo.

where winget >nul 2>&1
if errorlevel 1 goto :python_download

echo          Installing Python 3.13 with winget...
echo.
winget install --id Python.Python.3.13 -e --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity
call :refresh_path
call :find_python
if defined PY (
    echo.
    echo   [ok]   Python installed.
    exit /b 0
)
echo.
echo          winget could not do it - trying the official installer.
echo.

:python_download
echo          Downloading Python 3.13 from python.org  ^(29 MB^)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; $u='%PY_URL%'; $o=Join-Path $env:TEMP 'vidnest-python-setup.exe'; try { Invoke-WebRequest -Uri $u -OutFile $o -UseBasicParsing } catch { exit 1 }; Start-Process -FilePath $o -ArgumentList '/passive InstallAllUsers=0 PrependPath=1 Include_test=0' -Wait; exit 0"
if errorlevel 1 goto :python_failed

call :refresh_path
call :find_python
if defined PY (
    echo.
    echo   [ok]   Python installed.
    exit /b 0
)

:python_failed
echo.
echo   [!!]   Python could not be installed automatically.
echo.
echo          Install it yourself:
echo            1. https://www.python.org/downloads/windows/
echo            2. Tick "Add python.exe to PATH" on the first screen
echo            3. Run start.bat again
echo.
set "MISSING_PY=1"
exit /b 1


:ensure_node
where node >nul 2>&1
if not errorlevel 1 (
    echo   [ok]   Node.js is installed.
    exit /b 0
)
if exist "%HERE%bin\node.exe" (
    echo   [ok]   Node.js is already in bin\node.exe.
    exit /b 0
)

echo   [..]   Node.js was not found - recommended.
choice /c YN /n /m "         Install Node.js LTS now? [Y/N] "
if errorlevel 2 goto :node_skipped

where winget >nul 2>&1
if errorlevel 1 goto :node_download

echo.
echo          Installing Node.js LTS with winget...
echo.
winget install --id OpenJS.NodeJS.LTS -e --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity
call :refresh_path
where node >nul 2>&1
if errorlevel 1 goto :node_download

echo.
echo   [ok]   Node.js installed.
exit /b 0

:node_download
echo.
echo          Downloading node.exe into bin\  ^(94 MB^)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; $u='%NODE_URL%'; New-Item -ItemType Directory -Force -Path '%HERE%bin' | Out-Null; try { Invoke-WebRequest -Uri $u -OutFile '%HERE%bin\node.exe' -UseBasicParsing } catch { exit 1 }; exit 0"
if errorlevel 1 goto :node_manual
"%HERE%bin\node.exe" --version >nul 2>&1
if errorlevel 1 goto :node_manual

echo   [ok]   Node.js installed into bin\node.exe.
exit /b 0

:node_skipped
echo   [--]   Skipped. YouTube may offer fewer formats.
exit /b 0

:node_manual
echo.
echo   [--]   Node.js could not be installed. Get the LTS version from
echo          https://nodejs.org/ and run start.bat again.
exit /b 0


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


rem Rebuilds PATH so freshly installed tools are visible without a reboot.
:refresh_path
for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%P"
if exist "%ProgramFiles%\nodejs\node.exe" set "PATH=%ProgramFiles%\nodejs;%PATH%"
if exist "%LOCALAPPDATA%\Programs\Python" set "PATH=%LOCALAPPDATA%\Programs\Python;%PATH%"
exit /b 0
