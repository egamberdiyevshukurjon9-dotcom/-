@echo off
chcp 65001 >nul
setlocal
set "VOICE_ROOT=%~dp0"
set "APP=%~dp0VoiceApp"
set "PY=%APP%\runtime\python.exe"
title Ovozli prezentatsiya - tekshiruv

if not exist "%PY%" (
    echo VoiceApp\runtime\python.exe topilmadi.
    echo Arxivni TO'LIQ ochib, hamma fayllarni fleshkaga nusxalaganingizni tekshiring.
    goto :fail
)

echo ===== 1/2: kompyuterni tekshirish =====
"%PY%" -X utf8 -B "%APP%\app\presentation_voice.py" --selftest
echo.
echo ===== 2/2: mikrofon (8 soniya davomida gapiring) =====
"%PY%" -X utf8 -B "%APP%\app\presentation_voice.py" --mic-test
:fail
echo.
echo Buyruqlarni prezentatsiyasiz sinash uchun TEST_VOICE.bat ni ishga tushiring.
if defined VOICE_NOPAUSE goto :eof
pause
