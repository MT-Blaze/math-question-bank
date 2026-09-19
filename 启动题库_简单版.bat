@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PORT=27182
echo [start] free port %PORT% ...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr LISTENING') do (
    echo   kill PID %%p
    taskkill /F /PID %%p >nul 2>&1
)
timeout /t 1 /nobreak >nul
echo [start] launch uvicorn ...
set PYTHONIOENCODING=utf-8
set NO_COLOR=1
start "MathBank" /MIN "%CD%\venv\Scripts\python.exe" -B -X utf8 -u -m uvicorn main:app --host 127.0.0.1 --port %PORT% --no-access-log --log-level critical
echo [start] wait for service ready ...
:waitloop
timeout /t 1 /nobreak >nul
powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/healthz' -UseBasicParsing -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
if errorlevel 1 goto waitloop
echo [start] service ready, open browser ...
start "" "http://127.0.0.1:%PORT%/"
echo [start] done, minimizing this window ...
powershell -Command "Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public class Win{[DllImport(\"user32.dll\")]public static extern bool ShowWindow(IntPtr hWnd,int nCmdShow);}'; [Win]::ShowWindow((Get-Process -Id $PID).MainWindowHandle, 6)"
