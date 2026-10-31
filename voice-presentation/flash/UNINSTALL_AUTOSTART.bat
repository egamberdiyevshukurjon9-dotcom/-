@echo off
setlocal
set "TARGET=%ProgramData%\VoicePresentation"
set "LAUNCHER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\VoicePresentationWatcher.bat"
if exist "%LAUNCHER%" del "%LAUNCHER%"
if exist "%TARGET%" rmdir /s /q "%TARGET%"
if exist "%TARGET%" (
    echo Ba'zi fayllar band (dastur hozir ishlayapti). Kompyuterni qayta yoqing va yana ishga tushiring.
) else (
    echo Avtomatik yoqish o'chirildi.
)
echo.
if defined VOICE_NOPAUSE goto :eof
pause
