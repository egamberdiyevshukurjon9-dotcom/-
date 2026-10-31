@echo off
chcp 65001 >nul
setlocal
set "VOICE_ROOT=%~dp0"
set "APP=%~dp0VoiceApp"
set "PY=%APP%\runtime\python.exe"
set "RC=1"
title Ovozli prezentatsiya - telefon pulti
if not exist "%PY%" (
    echo VoiceApp\runtime\python.exe topilmadi.
    echo Arxivni TO'LIQ ochib, hamma fayllarni fleshkaga nusxalaganingizni tekshiring.
    goto :fail
)
echo Telefon pulti yoqiladi. Telefon va kompyuter BIR XIL Wi-Fi tarmog'ida bo'lsin.
echo Quyida chiqadigan havolani telefon brauzerida oching.
"%PY%" -X utf8 -B "%APP%\app\presentation_voice.py" --remote %*
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" exit /b 0
echo.
echo Dastur xato bilan tugadi (kod %RC%). Sababini topish uchun TEST.bat ni ishga tushiring.
:fail
echo.
if not defined VOICE_NOPAUSE pause
exit /b %RC%
