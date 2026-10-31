@echo off
chcp 65001 >nul
setlocal
set "TARGET=%LOCALAPPDATA%\VoicePresentation"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LAUNCHER=%STARTUP%\VoicePresentationWatcher.bat"

set "PYEXE="
set "PYARGS="
where py >nul 2>nul && (set "PYEXE=py" & set "PYARGS=-3")
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE goto :nopython

echo [1/3] Kutubxonalar o'rnatilmoqda...
%PYEXE% %PYARGS% -m pip install --user -r "%~dp0requirements.txt"
if errorlevel 1 goto :pipfail

echo [2/3] Fayllar nusxalanmoqda: %TARGET%
if not exist "%TARGET%" mkdir "%TARGET%"
copy /y "%~dp0presentation_voice.py" "%TARGET%" >nul
copy /y "%~dp0commands.py" "%TARGET%" >nul
copy /y "%~dp0backends.py" "%TARGET%" >nul
copy /y "%~dp0router.py" "%TARGET%" >nul
if not exist "%TARGET%\config.json" copy "%~dp0config.json" "%TARGET%" >nul

echo [3/3] Windows ishga tushganda avtomatik yoqish sozlanmoqda...
>"%LAUNCHER%" echo @echo off
>>"%LAUNCHER%" echo start "" /min %PYEXE% %PYARGS% "%TARGET%\presentation_voice.py" --watch

start "" /min %PYEXE% %PYARGS% "%TARGET%\presentation_voice.py" --watch
echo.
echo Tayyor. Endi fleshka ulansa (ichida presentation.pptx bo'lsa) taqdimot o'zi ochiladi.
echo Sozlamalar: %TARGET%\config.json
echo O'chirish uchun: UNINSTALL_AUTOSTART.bat
goto :end

:nopython
echo Python topilmadi. https://www.python.org/downloads/ dan o'rnating ("Add python.exe to PATH" belgilansin).
goto :end

:pipfail
echo O'rnatishda xatolik. Internetni tekshiring. PyAudio o'rnatilmasa Python 3.11 yoki 3.12 ishlating.

:end
echo.
pause
