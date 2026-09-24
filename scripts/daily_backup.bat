@echo off
REM Daily PostgreSQL -> SQLite backup for the 8-K Watcher.
REM Scheduled by Windows Task Scheduler; runs outside Django/scheduler processes.

REM Go to the backend folder: the parent of this script's folder (scripts\).
REM Works from any drive or folder, with no editing.
cd /d "%~dp0.."

REM Pick Python: the project's virtual env if it exists, otherwise the
REM system "python". Set BACKUP_PYTHON to force a specific python.exe.
set "PYTHON_EXE=python"
if exist "%CD%\.venv\Scripts\python.exe" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if exist "%CD%\venv\Scripts\python.exe" set "PYTHON_EXE=%CD%\venv\Scripts\python.exe"
if defined BACKUP_PYTHON set "PYTHON_EXE=%BACKUP_PYTHON%"

if not exist "backups" mkdir "backups"

echo [%date% %time%] Backup starting (python: %PYTHON_EXE%) >> backups\backup.log
"%PYTHON_EXE%" manage.py backup_to_sqlite --keep 7 >> backups\backup.log 2>&1
echo [%date% %time%] Exit code %errorlevel% >> backups\backup.log