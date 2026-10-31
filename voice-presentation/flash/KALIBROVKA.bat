@echo off
chcp 65001 >nul
setlocal
set "VOICE_ROOT=%~dp0"
set "APP=%~dp0VoiceApp"
set "PY=%APP%\runtime\python.exe"
set "RC=1"
title Ovozli prezentatsiya - kalibrovka
if not exist "%PY%" (
    echo VoiceApp\runtime\python.exe topilmadi.
    echo Arxivni TO'LIQ ochib, hamma fayllarni fleshkaga nusxalaganingizni tekshiring.
    goto :fail
)
echo Kalibrovka: tizim sizning ovozingizda qanchalik aniq ishlashini o'lchaydi.
echo Tinch xonada, odatdagi masofada gapiring. Taxminan 3 daqiqa.
"%PY%" -X utf8 -B "%APP%\app\presentation_voice.py" --calibrate %*
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" exit /b 0
echo.
echo Dastur xato bilan tugadi (kod %RC%). Sababini topish uchun TEST.bat ni ishga tushiring.
:fail
echo.
if not defined VOICE_NOPAUSE pause
exit /b %RC%
