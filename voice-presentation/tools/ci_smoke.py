# -*- coding: utf-8 -*-
"""Йиғилган пакетни Windows'да текшириш (GitHub Actions). Пакетнинг ўз Python'ида ишлайди.

Мақсад: "флешкадаги нусха тоза компьютерда импорт ва COM даражасида ишлайдими" деган саволга
жавоб бериш. Қуйидаги [FAIL] қаторлар CI'ни йиқитади, [info] қаторлар фақат маълумот учун.
"""
import ctypes
import importlib
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent
failures = []


def check(name, func, critical=True):
    try:
        result = func()
        print("[ok]   %s%s" % (name, (": %s" % result) if result else ""))
    except Exception as error:
        print("[%s] %s: %r" % ("FAIL" if critical else "info", name, error))
        if critical:
            failures.append(name)


def imports():
    for module in ("ctypes", "winsound", "winreg", "vosk", "sounddevice", "speech_recognition",
                   "win32com.client", "pythoncom", "pywintypes", "requests", "tqdm", "srt", "cffi",
                   "urllib.request", "json", "wave", "queue", "threading"):
        importlib.import_module(module)
    return "%d модуль" % 18


def com_works():
    import win32com.client
    fso = win32com.client.Dispatch("Scripting.FileSystemObject")
    return "TEMP=%s" % fso.GetSpecialFolder(2)


def powerpoint_progid():
    import winreg
    winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "PowerPoint.Application"))
    return "ўрнатилган"


def sendinput():
    sys.path.insert(0, str(APP.parent / "app"))
    import winkeys
    winkeys.press("shift")
    return "SendInput ишлади"


def portaudio():
    import sounddevice as sd
    inputs = [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
    return "%d та кириш қурилмаси; hostapi: %s" % (len(inputs), [a["name"] for a in sd.query_hostapis()])


def drives():
    sys.path.insert(0, str(APP.parent / "app"))
    import presentation_voice as pv
    return "флешкалар: %s" % pv.list_drives()


def vosk_loads():
    import vosk
    return "libvosk юкланди, %s" % getattr(vosk, "__file__", "?")


def lib_paths():
    # Windows'да DLL'лар пакет ичидан топилиши керак (тизим Python'идан эмас)
    import vosk, sounddevice, pythoncom
    return "\n         ".join(str(m.__file__) for m in (vosk, sounddevice, pythoncom))


check("импортлар", imports)
check("win32com (COM) ишлайди", com_works)
check("Vosk кутубхонаси", vosk_loads)
check("модуллар пакет ичидан юкланган", lib_paths)
check("PortAudio / микрофонлар", portaudio, critical=False)
check("SendInput (тугма босиш)", sendinput, critical=False)
check("PowerPoint ўрнатилганми", powerpoint_progid, critical=False)
check("флешка кузатуви", drives, critical=False)
print("Python:", sys.version)
print("Platform:", sys.platform, "ctypes.windll:", hasattr(ctypes, "windll"))
sys.exit(1 if failures else 0)
