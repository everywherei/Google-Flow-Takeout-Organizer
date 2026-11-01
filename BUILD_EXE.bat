@echo off
cd /d "%~dp0"
py -m pip install pyinstaller
py -m PyInstaller --onefile --console --name OrganizeFlow organize_flow.py
echo.
echo Done: copy dist\OrganizeFlow.exe into your Takeout folder and double-click it.
pause
