@echo off
chcp 65001 >nul
setlocal
set "VOICE_ROOT=%~dp0"
set "APP=%~dp0VoiceApp"
set "PY=%APP%\runtime\python.exe"
set "RC=1"
if not exist "%PY%" (
    echo VoiceApp\runtime\python.exe topilmadi.
    echo Arxivni TO'LIQ ochib, hamma fayllarni fleshkaga nusxalaganingizni tekshiring.
    goto :fail
)
if exist "%APP%\reports" (
    start "" "%APP%\reports"
) else (
    echo Hisobotlar hali yo'q: ular namoyish tugagach VoiceApp\reports papkasida paydo bo'ladi.
)
set "RC=0"
:fail
echo.
if not defined VOICE_NOPAUSE pause
exit /b %RC%
