@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Ovozli prezentatsiya

rem 1) Tayyor .exe bo'lsa (build_exe.bat yaratadi) - Python kerak emas
if exist "%~dp0VoicePresentation.exe" (
    "%~dp0VoicePresentation.exe" %*
    goto :end
)

rem 2) Python ni topish
set "PYEXE="
set "PYARGS="
where py >nul 2>nul && (set "PYEXE=py" & set "PYARGS=-3")
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
    echo Python topilmadi. https://www.python.org/downloads/ dan o'rnating
    echo ^("Add python.exe to PATH" katagini belgilang^), yoki VoicePresentation.exe ishlating.
    goto :end
)

rem 3) Kutubxonalar (faqat birinchi marta, internet kerak)
"%PYEXE%" %PYARGS% -c "import speech_recognition, pyaudio, pyautogui, win32com.client" >nul 2>nul
if errorlevel 1 (
    echo Kutubxonalar o'rnatilmoqda...
    "%PYEXE%" %PYARGS% -m pip install --user -r requirements.txt
    if errorlevel 1 (
        echo O'rnatishda xatolik. Internetni tekshiring. PyAudio o'rnatilmasa Python 3.11 yoki 3.12 ishlating.
        goto :end
    )
)

"%PYEXE%" %PYARGS% presentation_voice.py %*

:end
echo.
pause
