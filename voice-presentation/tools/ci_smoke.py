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


def uz_model_covers_core_commands():
    """Ўзбек Vosk моделида асосий буйруқлар учун камида битта сўз бўлиши шарт."""
    import os
    package = Path(os.environ["PKG"]) / "VoiceApp"
    sys.path.insert(0, str(package / "app"))
    import presentation_voice as pv
    from commands import CommandParser
    from engines import VoskEngine, discover_models
    models = discover_models(package / "models", ["uz"])
    assert models, "models/uz топилмади"
    engine = VoskEngine(dict(pv.DEFAULT_CONFIG), CommandParser(), lambda command: None, models)
    engine.load()
    report = engine.report["uz"]
    core = ("next", "prev", "first", "last", "black", "white", "exit", "start", "help")
    empty = [intent for intent in core if not report["coverage"][intent]]
    assert not empty, "ўзбек моделида сўзи йўқ буйруқлар: %s" % empty
    for word in ("slayd", "besh", "bir"):
        assert word not in report["missing"], "ўзбек моделида «%s» сўзи йўқ" % word
    return "%d та сўз; грамматика: %s" % (report["grammar_words"], report["grammar"])


def new_modules_work():
    """Телефон пульти (ҳақиқий HTTP), ҳисобот ва push-to-talk Windows API'си."""
    import json
    import tempfile
    import urllib.request
    sys.path.insert(0, str(APP.parent / "app"))
    from analytics import SessionLog
    from hotkeys import PushToTalk, windows_key_state
    from remote import RemoteServer, SessionState, lan_address

    received = []
    server = RemoteServer(received.append, SessionState(), "tok", host="127.0.0.1", port=0)
    port = server.start()
    try:
        request = urllib.request.Request("http://127.0.0.1:%d/api/command/next?t=tok" % port, data=b"", method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            assert json.load(response)["ok"] and received, "буйруқ навбатга тушмади"
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/status" % port, timeout=5)
            raise AssertionError("токенсиз сўров рад этилмади")
        except urllib.error.HTTPError as error:
            assert error.code == 401
    finally:
        server.stop()
    windows_key_state(0x78)                      # F9: GetAsyncKeyState чақируви хатосиз
    PushToTalk("f9").poll_once()                 # ҳақиқий Windows ҳолат функцияси билан
    with tempfile.TemporaryDirectory() as tmp:
        log = SessionLog()
        log.observe_position((1, 3))
        assert len(log.save(tmp)) == 3
    return "пульт HTTP, GetAsyncKeyState, ҳисобот сақлаш; LAN манзили %s" % lan_address()


check("импортлар", imports)
check("win32com (COM) ишлайди", com_works)
check("Vosk кутубхонаси", vosk_loads)
check("модуллар пакет ичидан юкланган", lib_paths)
check("ўзбек модели асосий буйруқларни билади", uz_model_covers_core_commands)
check("пульт, push-to-talk, ҳисобот", new_modules_work)
check("PortAudio / микрофонлар", portaudio, critical=False)
check("SendInput (тугма босиш)", sendinput, critical=False)
check("PowerPoint ўрнатилганми", powerpoint_progid, critical=False)
check("флешка кузатуви", drives, critical=False)
print("Python:", sys.version)
print("Platform:", sys.platform, "ctypes.windll:", hasattr(ctypes, "windll"))
sys.exit(1 if failures else 0)
