@echo off
chcp 65001 >nul
setlocal
set "VOICE_ROOT=%~dp0"
set "APP=%~dp0VoiceApp"
set "PY=%APP%\runtime\python.exe"
title Ovozli prezentatsiya - ovoz sinovi

if not exist "%PY%" (
    echo VoiceApp\runtime\python.exe topilmadi.
    echo Arxivni TO'LIQ ochib, hamma fayllarni fleshkaga nusxalaganingizni tekshiring.
    goto :fail
)

echo Prezentatsiyasiz sinov: buyruqlarni ayting, dastur nima eshitganini ko'rsatadi.
"%PY%" -X utf8 -B "%APP%\app\presentation_voice.py" --test
:fail
echo.
if defined VOICE_NOPAUSE goto :eof
pause
