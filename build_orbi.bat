@echo off
setlocal EnableDelayedExpansion
title Building Orbi

set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"
cd /d "%HERE%"

echo.
echo ============================================================
echo   Orbi build script
echo   Source:  %HERE%\orbi.py
echo   Output:  %HERE%\Orbi\Orbi.exe
echo ============================================================
echo.

if not exist "%HERE%\orbi.py" (
    echo [X] orbi.py not found in %HERE%
    echo     Put this .bat in the same folder as orbi.py and try again.
    echo.
    pause
    exit /b 1
)

REM ---------------------------------------------------------------------------
REM 1. Find Python
REM ---------------------------------------------------------------------------
echo [1/4] Checking for Python...
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py"
)
if not defined PY (
    echo [X] Python not found on PATH.
    echo     Install Python 3.10+ from https://python.org and try again.
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('%PY% --version 2^>^&1') do echo       Found %%v

REM ---------------------------------------------------------------------------
REM 2. Check dependencies -- install only what is missing
REM ---------------------------------------------------------------------------
echo [2/4] Checking dependencies...

set "MISSING="
%PY% -c "import PySide6" 2>nul || set "MISSING=!MISSING! PySide6"
%PY% -c "import psutil"  2>nul || set "MISSING=!MISSING! psutil"
%PY% -c "import PyInstaller" 2>nul || set "MISSING=!MISSING! pyinstaller"

if "%MISSING%"=="" (
    echo       All dependencies are already installed.
) else (
    echo       Missing:!MISSING!
    echo       Installing...
    %PY% -m pip install!MISSING!
    if errorlevel 1 (
        echo [X] Dependency install failed.
        echo     If you are offline, install them manually and re-run.
        echo     Required: PySide6 psutil pyinstaller
        echo.
        pause
        exit /b 1
    )
)

REM ---------------------------------------------------------------------------
REM 3. Clean old build artifacts
REM ---------------------------------------------------------------------------
echo [3/4] Cleaning old build artifacts...
if exist "%HERE%\build"     rmdir /s /q "%HERE%\build"
if exist "%HERE%\Orbi"      rmdir /s /q "%HERE%\Orbi"
if exist "%HERE%\Orbi.spec" del /q "%HERE%\Orbi.spec"

REM ---------------------------------------------------------------------------
REM 4. Build
REM ---------------------------------------------------------------------------
echo [4/4] Building Orbi.exe (this takes 1-3 minutes on first run)...
%PY% -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --name Orbi ^
    --distpath "%HERE%" ^
    --workpath "%HERE%\build" ^
    --specpath "%HERE%\build" ^
    --hidden-import PySide6.QtNetwork ^
    --exclude-module tkinter ^
    --exclude-module PySide6.QtWebEngineCore ^
    --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtQuick ^
    --exclude-module PySide6.Qt3DCore ^
    --exclude-module PySide6.QtMultimedia ^
    --exclude-module PySide6.QtCharts ^
    --exclude-module PySide6.QtDataVisualization ^
    --exclude-module PySide6.QtDesigner ^
    --exclude-module PySide6.QtTest ^
    "%HERE%\orbi.py"

if errorlevel 1 (
    echo.
    echo [X] Build failed. Scroll up and read the error message.
    echo.
    pause
    exit /b 1
)

if exist "%HERE%\build" rmdir /s /q "%HERE%\build"

echo.
echo ============================================================
echo   BUILD COMPLETE
echo.
echo   Your app is here:
echo     %HERE%\Orbi\Orbi.exe
echo.
echo   To give it to someone else:
echo     1. Right-click the "Orbi" folder -^> Send to -^> Compressed
echo        (zipped) folder.
echo     2. Send them the resulting .zip.
echo     3. They unzip it anywhere and run Orbi.exe.
echo        On first launch: right-click Orbi -^> Settings -^>
echo        System -^> check "Start with Windows".
echo.
echo   The target machine does NOT need Python.
echo ============================================================
echo.
pause