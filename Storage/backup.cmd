@echo off
rem Dumps the metal, general and it databases to Storage\backups\ocrdb-<yyyy-MM-dd>-<env>.dump.
rem Run from any directory; output always goes to the backups folder next to this script.
rem Uses the shared test password "ocr" unless PGPASSWORD is already set in the session.

setlocal
if not defined PGPASSWORD set "PGPASSWORD=ocr"

rem Locale-independent date (the %DATE% format varies per machine)
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "TODAY=%%d"

set "OUT=%~dp0backups"
if not exist "%OUT%" mkdir "%OUT%"

call :dump 5432 metal   || exit /b 1
call :dump 5434 general || exit /b 1
call :dump 5436 it      || exit /b 1
echo Done.
exit /b 0

:dump
echo Dumping %2 (port %1)...
pg_dump -h localhost -p %1 -U ocr -Fc -f "%OUT%\ocrdb-%TODAY%-%2.dump" ocrdb
if errorlevel 1 (
    echo pg_dump failed for %2 1>&2
    exit /b 1
)
exit /b 0
