@echo off
chcp 65001 >nul
setlocal
set "RC=1"
set "SRC=%~dp0VoiceApp"
set "TARGET=%ProgramData%\VoicePresentation"
set "LAUNCHER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\VoicePresentationWatcher.bat"

if not exist "%SRC%\runtime\python.exe" (
    echo VoiceApp topilmadi. Bu faylni fleshkadagi START.bat yonidan ishga tushiring.
    goto :end
)
echo [1/3] Fayllar nusxalanmoqda: %TARGET%
echo       (taxminan 200 MB, bir necha daqiqa ketishi mumkin)
robocopy "%SRC%" "%TARGET%" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :copyfail

echo [2/3] Windows ishga tushganda avtomatik yoqish sozlanmoqda...
>"%LAUNCHER%" echo @echo off
>>"%LAUNCHER%" echo start "" /min "%TARGET%\runtime\python.exe" -X utf8 -B "%TARGET%\app\presentation_voice.py" --watch

echo [3/3] Hozir ishga tushirilmoqda...
start "" /min "%TARGET%\runtime\python.exe" -X utf8 -B "%TARGET%\app\presentation_voice.py" --watch
echo.
echo Tayyor. Endi fleshka ulansa (ichida presentation.pptx bo'lsa) taqdimot o'zi ochiladi.
echo Sozlamalar fayli: %TARGET%\config.json
echo O'chirish uchun: UNINSTALL_AUTOSTART.bat
set "RC=0"
goto :end

:copyfail
echo Nusxalashda xatolik. Disk joyi yetarli ekanini tekshiring.

:end
echo.
if not defined VOICE_NOPAUSE pause
exit /b %RC%
