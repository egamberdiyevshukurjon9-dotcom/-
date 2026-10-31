@echo off
chcp 65001 >nul
setlocal
set "VOICE_ROOT=%~dp0"
set "APP=%~dp0VoiceApp"
set "PY=%APP%\runtime\python.exe"
title Ovozli prezentatsiya

if not exist "%PY%" (
    echo VoiceApp\runtime\python.exe topilmadi.
    echo Arxivni TO'LIQ ochib, hamma fayllarni fleshkaga nusxalaganingizni tekshiring.
    goto :fail
)

"%PY%" -X utf8 -B "%APP%\app\presentation_voice.py" %*
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" goto :eof
echo.
echo Dastur xato bilan tugadi (kod %RC%). Sababini topish uchun TEST.bat ni ishga tushiring.
:fail
echo.
if defined VOICE_NOPAUSE goto :eof
pause
