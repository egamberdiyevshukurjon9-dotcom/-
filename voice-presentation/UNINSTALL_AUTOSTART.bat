@echo off
setlocal
set "TARGET=%LOCALAPPDATA%\VoicePresentation"
set "LAUNCHER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\VoicePresentationWatcher.bat"
if exist "%LAUNCHER%" del "%LAUNCHER%"
if exist "%TARGET%" rmdir /s /q "%TARGET%"
echo Avtomatik yoqish o'chirildi. Hozir ishlayotgan oyna (tepadagi minimallashtirilgan) bo'lsa, uni yoping.
echo.
pause
