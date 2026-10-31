@echo off
rem Python o'rnatilmagan kompyuterlar uchun bitta VoicePresentation.exe yaratadi.
rem Faqat Windows'da, Python o'rnatilgan kompyuterda bir marta ishga tushiring.
cd /d "%~dp0"
python -m pip install --user -r requirements.txt pyinstaller
if errorlevel 1 goto :end
python -m PyInstaller --onefile --name VoicePresentation --collect-all speech_recognition --hidden-import win32timezone presentation_voice.py
if errorlevel 1 goto :end
copy /y dist\VoicePresentation.exe "%~dp0" >nul
echo.
echo Tayyor: VoicePresentation.exe - uni fleshkaga START.bat yoniga ko'chiring.
:end
pause
