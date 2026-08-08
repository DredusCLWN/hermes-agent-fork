$content = @"
@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title Hermes Restart

set "PROJECT_ROOT=%~dp0"
set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "SETUP_MARKER=%PROJECT_ROOT%\.hermes-setup-done"

if not exist "%SETUP_MARKER%" (
    echo ============================================
    echo   Hermes Agent - First Run Setup
    echo ============================================
    echo.

    cd /d "%PROJECT_ROOT%"

    echo [1/4] Cleaning stale artifacts...
    if exist ".venv" (
        echo   Removing .venv...
        rd /s /q ".venv" 2>nul
    )
    if exist "apps\desktop\node_modules" (
        echo   Removing apps\desktop\node_modules...
        rd /s /q "apps\desktop\node_modules" 2>nul
    )
    if exist "apps\desktop\dist" (
        echo   Removing apps\desktop\dist...
        rd /s /q "apps\desktop\dist" 2>nul
    )
    if exist "apps\shared\node_modules" (
        echo   Removing apps\shared\node_modules...
        rd /s /q "apps\shared\node_modules" 2>nul
    )
    echo   Done.
    echo.

    echo [2/4] Setting up Python environment...
    set "PYTHON_CMD="
    set "FOUND_PYTHON=0"

    where uv >nul 2>&1
    if !errorlevel! equ 0 (
        echo   Found uv, creating venv...
        uv venv 2>nul
        if !errorlevel! equ 0 (
            uv pip install -e .
            if !errorlevel! equ 0 (
                set "FOUND_PYTHON=1"
            )
        )
    )

    if "!FOUND_PYTHON!"=="0" (
        for %%v in (311 312) do (
            if "!FOUND_PYTHON!"=="0" (
                if exist "%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe" (
                    set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python%%v\python.exe"
                    set "FOUND_PYTHON=1"
                )
            )
            if "!FOUND_PYTHON!"=="0" (
                if exist "C:\Program Files\Python%%v\python.exe" (
                    set "PYTHON_CMD=C:\Program Files\Python%%v\python.exe"
                    set "FOUND_PYTHON=1"
                )
            )
        )

        if "!FOUND_PYTHON!"=="0" (
            where py >nul 2>&1
            if !errorlevel! equ 0 (
                for /f "tokens=*" %%p in ('py -3.11 -c "import sys; print(sys.executable)" 2^>nul') do (
                    if not "%%p"=="" set "PYTHON_CMD=%%p" && set "FOUND_PYTHON=1"
                )
                if "!FOUND_PYTHON!"=="0" (
                    for /f "tokens=*" %%p in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do (
                        if not "%%p"=="" set "PYTHON_CMD=%%p" && set "FOUND_PYTHON=1"
                    )
                )
            )
        )

        if "!FOUND_PYTHON!"=="0" (
            for %%c in (python3 python) do (
                if "!FOUND_PYTHON!"=="0" (
                    where %%c >nul 2>&1
                    if !errorlevel! equ 0 (
                        for /f "tokens=*" %%p in ('%%c -c "import sys; v=sys.version_info; print(sys.executable if v.major==3 and v.minor in (11,12) else '')" 2^>nul') do (
                            if not "%%p"=="" set "PYTHON_CMD=%%p" && set "FOUND_PYTHON=1"
                        )
                    )
                )
            )
        )

        if "!FOUND_PYTHON!"=="0" (
            echo.
            echo   ERROR: Python 3.11 or 3.12 not found!
            echo   Install from https://www.python.org/downloads/
            echo   Or run:  pip install uv
            pause
            exit /b 1
        )

        echo   Found Python: !PYTHON_CMD!
        echo   Creating venv...
        "!PYTHON_CMD!" -m venv .venv
        if !errorlevel! neq 0 (
            echo   ERROR: venv creation failed.
            pause
            exit /b 1
        )
        ".venv\Scripts\python.exe" -m pip install --upgrade pip
        echo   Installing dependencies (may take a few minutes)...
        ".venv\Scripts\pip.exe" install -e .
        if !errorlevel! neq 0 (
            echo   ERROR: Python dependency installation failed.
            pause
            exit /b 1
        )
    )
    echo   Python environment ready.
    echo.

    echo [3/4] Installing desktop app dependencies...
    where node >nul 2>&1
    if !errorlevel! neq 0 (
        echo   ERROR: Node.js not found! Install Node.js 22+ from https://nodejs.org/
        pause
        exit /b 1
    )
    pushd "apps\desktop"
    call npm install
    if !errorlevel! neq 0 (
        echo   ERROR: npm install failed.
        popd
        pause
        exit /b 1
    )
    echo   Building desktop app...
    call npm run build
    if !errorlevel! neq 0 (
        echo   ERROR: npm run build failed.
        popd
        pause
        exit /b 1
    )
    popd
    echo   Desktop app ready.
    echo.

    echo [4/4] Finalizing...
    echo setup complete > "%SETUP_MARKER%"
    echo.
    echo ============================================
    echo   Setup complete! Starting Hermes...
    echo ============================================
    echo.
)

echo [*] Killing Hermes processes...
taskkill /F /IM electron.exe >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5174" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8501" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 3 /nobreak >nul

echo [*] Starting Hermes desktop app...
cd /d "%PROJECT_ROOT%\apps\desktop"
start "Hermes Dev" cmd /c "npm run dev"

echo [*] Hermes is starting in a new window.
echo [*] This window will close in 3 seconds.
timeout /t 3 /nobreak >nul
pause
exit
"@

[System.IO.File]::WriteAllText("$PWD\restart-hermes.bat", $content, [System.Text.Encoding]::ASCII)
Write-Host "restart-hermes.bat created with CRLF line endings and ASCII encoding."
