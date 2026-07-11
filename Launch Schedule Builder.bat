@echo off
cd /d "%~dp0"
py -3.12 -m schedule_builder.gui
if errorlevel 1 pause
