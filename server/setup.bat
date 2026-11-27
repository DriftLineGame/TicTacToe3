@echo off
title Tic-Tac-Toe3 setup
cd /d "%~dp0"
echo Installing the Python package the server needs...
python -m pip install --upgrade -r requirements.txt
echo.
echo Installing Cloudflare tunnel (cloudflared)...
winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
echo.
echo Done! Close this window and double-click start-server.bat
pause
