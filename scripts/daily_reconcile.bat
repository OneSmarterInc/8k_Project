@echo off
REM Daily EDGAR daily-index reconciliation for the 8-K Watcher (D-02).
REM Scheduled by Windows Task Scheduler; runs outside Django/scheduler
REM processes. Read-only: it never modifies filings, runs, or any data.
REM
REM EDGAR publishes each daily index after the close (usually late
REM evening ET), so schedule this for the following morning.

REM Go to the backend folder: the parent of this script's folder (scripts\).
REM Works from any drive or folder, with no editing.
cd /d "%~dp0.."

REM Pick Python: the project's virtual env if it exists, otherwise the
REM system "python". Set RECONCILE_PYTHON to force a specific python.exe.
set "PYTHON_EXE=python"
if exist "%CD%\.venv\Scripts\python.exe" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if exist "%CD%\venv\Scripts\python.exe" set "PYTHON_EXE=%CD%\venv\Scripts\python.exe"
if exist "%CD%\Venv\Scripts\python.exe" set "PYTHON_EXE=%CD%\Venv\Scripts\python.exe"
if defined RECONCILE_PYTHON set "PYTHON_EXE=%RECONCILE_PYTHON%"

if not exist "reports\reconciliation" mkdir "reports\reconciliation"

echo [%date% %time%] Reconcile starting (python: %PYTHON_EXE%) >> reports\reconciliation\reconcile.log
"%PYTHON_EXE%" manage.py reconcile_daily_index --days 1 >> reports\reconciliation\reconcile.log 2>&1
echo [%date% %time%] Exit code %errorlevel% >> reports\reconciliation\reconcile.log
