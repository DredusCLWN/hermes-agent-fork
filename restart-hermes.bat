@echo off
chcp 65001 >nul 2>&1
title Hermes Restart

echo [*] Killing Hermes processes...

:: Kill Electron (desktop app)
taskkill /F /IM electron.exe >nul 2>&1

:: Kill vite dev server on port 5174
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5174" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Kill Hermes backend on common ports
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8501" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Wait for processes to die
timeout /t 3 /nobreak >nul

echo [*] Starting Hermes desktop app...
cd /d "d:\Hermes\hermes-agent\apps\desktop"

:: Launch npm run dev in a new window so this bat can close
start "Hermes Dev" cmd /c "npm run dev"

echo [*] Hermes is starting in a new window.
echo [*] This window will close in 3 seconds.
timeout /t 3 /nobreak >nul
exit
