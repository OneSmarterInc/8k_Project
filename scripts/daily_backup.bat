@echo off
REM Daily PostgreSQL -> SQLite backup for the 8-K Watcher.
REM Scheduled by Windows Task Scheduler; runs outside Django/scheduler processes.

cd /d D:\SECAIAGENTWatcher\8k-agentic-system\backend
if not exist backups mkdir backups

echo [%date% %time%] Backup starting >> backups\backup.log
"D:\SECAIAGENTWatcher\8k-agentic-system\backend\Venv\Scripts\python.exe" manage.py backup_to_sqlite --keep 7 >> backups\backup.log 2>&1
echo [%date% %time%] Exit code %errorlevel% >> backups\backup.log