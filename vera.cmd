@echo off
rem Launch Vera from cmd or PowerShell: vera [data folder]
set PYTHONIOENCODING=utf-8
where python >nul 2>nul
if %errorlevel%==0 (python "%~dp0system\vera_cli.py" %*) else (py -3 "%~dp0system\vera_cli.py" %*)
