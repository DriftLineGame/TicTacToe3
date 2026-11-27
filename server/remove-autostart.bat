@echo off
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Startup')+'\TicTacToe3 Server.lnk')"
echo Autostart removed.
pause
