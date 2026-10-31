# -*- coding: utf-8 -*-
"""Презентацияни флешка орқали очиш ва овоз билан бошқариш.

Ишлатиш:
    START.bat                                флешкадаги презентацияни очиб, овозни тинглайди
    presentation_voice.py --watch            фонда флешка уланишини кутади (кўп марта)
    presentation_voice.py --test             презентациясиз, микрофон ва буйруқларни синаш
    presentation_voice.py --selftest         компьютерда ҳамма нарса ишлашини текшириш
    presentation_voice.py --mic-test         микрофон баландлигини кўрсатиш
    presentation_voice.py --list-mics        микрофонлар рўйхати
    presentation_voice.py --wav f.wav --expect next   овоз файлини офлайн таниш синови
"""
import sys

sys.dont_write_bytecode = True  # флешкада __pycache__ ёзилмасин
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import queue  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from typing import List, Optional, Tuple  # noqa: E402

from audio import AudioInput, rms  # noqa: E402
from backends import Backend, DryRunBackend, KeyboardBackend, PowerPointBackend  # noqa: E402
from commands import Command, CommandParser  # noqa: E402
from engines import (VoskEngine, discover_models, load_engines, read_vocabulary,  # noqa: E402
                     read_wav_pcm)
from errors import AudioError, EngineError, SetupError  # noqa: E402
from router import HELP_TEXT, CommandRouter  # noqa: E402

log = logging.getLogger("voicepres")

PRESENTATION_EXTENSIONS = (".pptx", ".pptm", ".ppsx", ".ppt", ".pps", ".odp")
POWERPOINT_EXTENSIONS = (".pptx", ".pptm", ".ppsx", ".ppt", ".pps")
IGNORED_FOLDERS = {"voiceapp", "system volume information", "$recycle.bin"}

DEFAULT_CONFIG = {
    # --- таниш мотори ---
    "engine": "auto",                 # auto: Vosk (офлайн) бор бўлса шу, йўқса Google | vosk | google | both
    "vosk_models": ["uz", "ru", "en"],  # models/ папкасидаги қайси тиллар ишлатилсин
    "vosk_grammar": True,             # фақат буйруқ сўзларини қидириш (тезроқ ва аниқроқ)
    "vosk_alternatives": 3,
    "models_dir": "",                 # бўш бўлса: VoiceApp/models
    "languages": ["uz-UZ"],           # Google тиллари (["uz-UZ", "ru-RU"] — сўровлар кўпаяди)
    # --- микрофон ---
    "mic": None,                      # None | рақам | номнинг бир қисми ("Realtek"); --list-mics
    "min_energy": 300,                # Google режимида: шундан паст овоз жимлик ҳисобланади
    "pause_threshold": 0.6,           # жимлик шунча давом этса — жумла тугади
    "phrase_time_limit": 4.0,
    "max_command_seconds": 3.0,       # Google режимида: узунроқ нутқ буйруқ эмас
    # --- буйруқларни аниқлаш ---
    "max_words": 4,                   # шундан узун гаплар буйруқ ҳисобланмайди
    "cooldown_seconds": 1.5,          # бир хил буйруқ орасидаги минимал вақт
    "fuzzy_threshold": 0.85,          # 0..1; пастроқ = кўпроқ хато эшитишни кечиради
    "wake_word": "",                  # масалан "slayd": буйруқ шу сўздан бошланиши шарт
    "extra_words": {},                # масалан {"next": ["davay"]}
    # --- қўшимча ---
    "talk_minutes": 0,                # >0: чиқиш вақти; тугашига warn_minutes қолганда огоҳлантиради
    "warn_minutes": 1,
    "timer_beep": True,               # вақт огоҳлантиришида сигнал
    "beep": False,                    # ҳар қабул қилинган буйруқда сигнал
    "auto_default_seconds": 15,       # "avtomatik" деганда ҳар неча сонияда ўтсин
    "preferred_names": ["presentation", "taqdimot", "prezentatsiya"],
    "include_fixed_drives": False,    # флешка "қаттиқ диск" деб кўринса — true қилинг
    "open_delay_seconds": 6,          # заҳира (клавиатура) режимида очилишни кутиш
    "log_file": "",                   # масалан "voice_log.txt"
    "verbose": False,                 # буйруқ бўлмаган эшитилган матнни ҳам кўрсатади
}


# ---------------------------------------------------------------------------
# Йўллар, созламалар, журнал
# ---------------------------------------------------------------------------
def app_dir() -> Path:
    return Path(__file__).resolve().parent


def home_dir() -> Path:
    """Дастур папкаси (config.json ва models/ шу ерда): VoiceApp/ ёки лойиҳа илдизи."""
    directory = app_dir()
    return directory.parent if directory.name == "app" else directory


def root_dir() -> Path:
    """Презентация қидириладиган папка (флешканинг илдизи)."""
    env = os.environ.get("VOICE_ROOT", "").strip().strip('"')
    if env:
        return Path(env)  # "E:\\" ни "E:" га айлантирманг: бу Windows'да бошқа папка
    home = home_dir()
    return home.parent if home.name.lower() == "voiceapp" else home


def load_config(folder: Path) -> dict:
    config = dict(DEFAULT_CONFIG)
    path = folder / "config.json"
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(user, dict):
                raise ValueError("JSON объект бўлиши керак")
            if "mic_index" in user and "mic" not in user:  # эски номи
                user["mic"] = user.pop("mic_index")
            config.update({k: v for k, v in user.items() if not k.startswith("_")})
        except (OSError, ValueError) as error:
            print("config.json ўқилмади (%s) — стандарт созламалар ишлатилади" % error)
    if isinstance(config["languages"], str):
        config["languages"] = [config["languages"]]
    return config


def setup_logging(folder: Path, log_file: str) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    log.setLevel(logging.INFO)
    log.handlers.clear()
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
    log.addHandler(console)
    if log_file:
        try:
            file_handler = logging.FileHandler(folder / log_file, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s"))
            log.addHandler(file_handler)
        except OSError as error:
            log.warning("Журнал файли очилмади: %s", error)


def make_parser(config: dict) -> CommandParser:
    return CommandParser(
        threshold=config["fuzzy_threshold"],
        max_words=config["max_words"],
        wake_word=config["wake_word"],
        extra_words=config["extra_words"])


def models_root(config: dict) -> Path:
    return Path(config["models_dir"]) if config["models_dir"] else home_dir() / "models"


# ---------------------------------------------------------------------------
# Флешка ва презентацияни топиш
# ---------------------------------------------------------------------------
def find_presentation(folder: Path, preferred_names=("presentation",)) -> Optional[Path]:
    """Папкадан презентацияни топади: аввал ном бўйича, кейин энг янги файл.
    Фақат папканинг ўзи ва унинг бевосита ичидаги папкалар қаралади."""
    def candidates(directory: Path) -> List[Path]:
        try:
            return [p for p in directory.iterdir()
                    if p.is_file() and p.suffix.lower() in PRESENTATION_EXTENSIONS
                    and not p.name.startswith("~$")]
        except OSError:
            return []

    def pick(files: List[Path]) -> Optional[Path]:
        wanted = [n.lower() for n in preferred_names]
        for name in wanted:
            for path in files:
                if path.stem.lower() == name:
                    return path
        if files:
            return max(files, key=lambda p: p.stat().st_mtime)
        return None

    found = pick(candidates(folder))
    if found:
        return found
    try:
        subfolders = [p for p in folder.iterdir()
                      if p.is_dir() and not p.name.startswith((".", "$"))
                      and p.name.lower() not in IGNORED_FOLDERS]
    except OSError:
        return None
    for sub in sorted(subfolders):
        found = pick(candidates(sub))
        if found:
            return found
    return None


def list_drives(include_fixed: bool = False) -> List[Path]:
    """Уланган флешкаларнинг (олинадиган дисклар) рўйхати. Фақат Windows."""
    if sys.platform != "win32":
        return []
    import ctypes
    import string
    kernel32 = ctypes.windll.kernel32
    kernel32.SetErrorMode(1)  # бўш картридер учун "диск киритинг" ойнаси чиқмасин
    drive_removable, drive_fixed = 2, 3
    system_drive = os.environ.get("SystemDrive", "C:").upper()
    mask = kernel32.GetLogicalDrives()
    drives = []
    for index, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << index):
            continue
        root = "%s:\\" % letter
        kind = kernel32.GetDriveTypeW(root)
        if kind == drive_removable or (
                include_fixed and kind == drive_fixed and root[:2] != system_drive):
            drives.append(Path(root))
    return drives


def find_on_drives(config: dict) -> Optional[Path]:
    for drive in list_drives(config["include_fixed_drives"]):
        found = find_presentation(drive, config["preferred_names"])
        if found:
            return found
    return None


def wait_for_presentation(config: dict) -> Path:
    log.info("Флешка уланиши кутилмоқда... (тўхтатиш: Ctrl+C)")
    while True:
        found = find_on_drives(config)
        if found:
            return found
        time.sleep(2)


# ---------------------------------------------------------------------------
# Презентацияни очиш
# ---------------------------------------------------------------------------
def open_backend(path: Optional[Path], args, config: dict) -> Backend:
    """Презентацияни очиб, шоуни бошлайди. PowerPoint ишламаса — клавиатура режими."""
    if args.test:
        backend = DryRunBackend()
        backend.start()
        return backend

    if (not args.keys and sys.platform == "win32"
            and (path is None or path.suffix.lower() in POWERPOINT_EXTENSIONS)):
        last_error: Optional[Exception] = None
        for attempt in range(3):  # PowerPoint энди ишга тушаётганда "банд" деб жавоб бериши мумкин
            try:
                backend = PowerPointBackend()
                if path is None:
                    backend.attach()
                else:
                    backend.open(path)
                if not backend.is_running():
                    backend.start()
                log.info("PowerPoint орқали бошқарилмоқда")
                return backend
            except ImportError as error:
                last_error = error
                break
            except Exception as error:
                last_error = error
                time.sleep(1.5)
        log.warning("PowerPoint COM ишламади (%s) — клавиатура режимига ўтилди", last_error)

    try:
        backend = KeyboardBackend(open_delay=config["open_delay_seconds"])
        if path is not None:
            backend.open(path)
    except OSError as error:
        raise SetupError("Презентацияни очиб бўлмади (PowerPoint, LibreOffice ёки WPS "
                         "ўрнатилганми?): %s" % error)
    backend.start()
    log.info("Клавиатура режими: презентация ойнаси фаол бўлиб туриши керак")
    return backend


def beep(frequency: int = 1000, milliseconds: int = 60) -> None:
    try:
        import winsound
        winsound.Beep(frequency, milliseconds)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Асосий сессия
# ---------------------------------------------------------------------------
def run_session(path: Optional[Path], args, config: dict, parser: CommandParser) -> int:
    results: "queue.Queue" = queue.Queue()
    audio = AudioInput(config["mic"], status=log.warning)
    loaded: dict = {}

    def load_models() -> None:  # модель юкланиши презентация очилишига халақит бермасин
        try:
            loaded["engines"] = load_engines(config, parser, results.put, models_root(config))
        except Exception as error:
            loaded["error"] = error

    loader = threading.Thread(target=load_models, daemon=True)
    loader.start()
    voice_error: Optional[Exception] = None
    try:
        device = audio.start()
    except AudioError as error:
        voice_error, device = error, ""

    try:
        backend = open_backend(path, args, config)
    except BaseException:
        audio.stop()
        raise
    loader.join()
    voice_error = voice_error or loaded.get("error")
    if voice_error is not None:
        audio.stop()
        log.error("Овозли бошқарув ишга тушмади: %s", voice_error)
        log.error("Презентация очиқ қолди — уни қўлда бошқаринг. Муаммони топиш: TEST.bat")
        return 2

    engines = loaded["engines"]
    for engine in engines:
        engine.start(audio.subscribe())
    router = CommandRouter(
        backend, cooldown=config["cooldown_seconds"], talk_minutes=config["talk_minutes"],
        warn_minutes=config["warn_minutes"], auto_default_seconds=config["auto_default_seconds"])
    started = time.monotonic()
    log.info("Овозли бошқарув ишга тушди. Мотор: %s. Микрофон: %s",
             " + ".join(e.name for e in engines), device)
    log.info("«yordam» (ёрдам) десангиз буйруқлар рўйхати чиқади.")
    if args.test:
        log.info("\n%s", HELP_TEXT)

    try:
        while not router.should_exit:
            try:
                command: Optional[Command] = results.get(timeout=0.5)
            except queue.Empty:
                command = None
                if path is not None and not path.exists():
                    log.warning("Флешка узилди — тугатилмоқда")
                    break
            if command is not None:
                message = router.handle(command)
                if message:
                    log.info("%s", message)
                    if config["beep"]:
                        beep()
            for message in router.tick():
                log.info("%s", message)
                if config["timer_beep"] and router.needs_attention:
                    beep(800, 250)
    finally:
        audio.stop()
        for engine in engines:
            engine.stop()
        backend.close()
        minutes, seconds = divmod(int(time.monotonic() - started), 60)
        log.info("Намойиш давомийлиги: %02d:%02d", minutes, seconds)
    return 0


def watch(args, config: dict, parser: CommandParser) -> None:
    """Фонда ишлайди: флешка уланса — очади, чиқарилса — яна кутади."""
    log.info("Кузатув режими: флешка уланишини кутяпман (тўхтатиш: Ctrl+C)")
    handled = set()  # шу уланишда очиб бўлинган дисклар
    while True:
        drives = list_drives(config["include_fixed_drives"])
        handled &= set(drives)
        for drive in drives:
            if drive in handled:
                continue
            path = find_presentation(drive, config["preferred_names"])
            if path:
                handled.add(drive)
                log.info("Презентация топилди: %s", path)
                try:
                    run_session(path, args, config, parser)
                except Exception as error:
                    log.error("Сессия хатоси: %s", error)
                break
        time.sleep(2)


# ---------------------------------------------------------------------------
# Диагностика режимлари
# ---------------------------------------------------------------------------
def list_microphones() -> None:
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        apis = sd.query_hostapis()
    except (ImportError, OSError) as error:
        raise AudioError("sounddevice юкланмади: %s" % error)
    default = sd.default.device[0]
    for index, device in enumerate(devices):
        if device["max_input_channels"] > 0:
            print("%3d%s  %s  (%s)" % (index, "*" if index == default else " ",
                                       device["name"], apis[device["hostapi"]]["name"]))
    print("(* — стандарт микрофон; config.json да \"mic\": рақам ёки номнинг бир қисми)")


def mic_test(config: dict, seconds: float = 8.0) -> int:
    audio = AudioInput(config["mic"], status=print)
    queue_ = audio.subscribe()
    print("Микрофон: %s" % audio.start())
    print("%d сония давомида гапириб кўринг (чизиқ — овоз баландлиги):" % seconds)
    end = time.monotonic() + seconds
    peak, quiet, last = 0.0, [], 0.0
    try:
        while time.monotonic() < end:
            try:
                stamp, block = queue_.get(timeout=1.0)
            except queue.Empty:
                print("Микрофондан бўлак келмаяпти!")
                return 1
            level = rms(block)
            peak = max(peak, level)
            if level < 200:
                quiet.append(level)
            if stamp - last >= 0.2:
                last = stamp
                print("%5d |%s" % (level, "#" * min(60, int(level / 60))))
    finally:
        audio.stop()
    noise = sum(quiet) / len(quiet) if quiet else 0
    print("\nШовқин ≈ %d, энг баланд овоз = %d" % (noise, peak))
    if peak == 0:
        print("[X] Умуман овоз йўқ: Windows → Махфийлик → Микрофон рухсатини текширинг.")
        return 1
    if peak < 800:
        print("[!] Овоз жуда паст: микрофонни яқинлаштиринг ёки Windows'да баландлигини ошириинг.")
        return 1
    print("[OK] Микрофон ишлаяпти.")
    return 0


def wav_test(path: str, expect: Optional[str], config: dict, parser: CommandParser) -> int:
    models = discover_models(models_root(config), config["vosk_models"] or None)
    if not models:
        raise EngineError("Vosk моделлари топилмади: %s" % models_root(config))
    engine = VoskEngine(config, parser, lambda command: None, models)
    engine.load()
    found: List[Command] = []
    for language, texts, command in engine.process_pcm(read_wav_pcm(path)):
        print("HEARD [%s]: %s -> %s" % (language, " | ".join(texts),
                                        "%s %s" % (command.intent, command.arg or "") if command else "-"))
        if command:
            found.append(command)
    if expect:
        intent, _, arg = expect.partition(":")
        wanted = [c for c in found if c.intent == intent and (not arg or c.arg == int(arg))]
        print("EXPECT %s: %s" % (expect, "OK" if wanted else "FAIL"))
        return 0 if wanted else 1
    return 0


def vocab_grep(prefixes: str, config: dict) -> int:
    """Модел луғатидан берилган бошланишдаги сўзларни кўрсатади (синоним танлаш учун)."""
    prefixes_ = [p.strip().lower() for p in prefixes.split(",") if p.strip()]
    for language, path in discover_models(models_root(config)).items():
        vocabulary = read_vocabulary(path) or {}
        print("== %s: %d та сўз" % (language, len(vocabulary)))
        for prefix in prefixes_:
            keys = sorted(k for k in vocabulary if k.startswith(prefix))
            raw = [w for k in keys for w in vocabulary[k]]
            print("  %s* (%d): %s" % (prefix, len(keys), ", ".join(raw[:30])))
    return 0


def selftest(config: dict, parser: CommandParser) -> int:
    """Шу компьютерда ҳамма нарса ишлашини текширади."""
    results: List[Tuple[str, str]] = []

    def add(level: str, text: str) -> None:
        results.append((level, text))
        print("[%s] %s" % (level, text))

    import importlib
    import platform
    add("OK", "Python %s, %s %s" % (platform.python_version(), platform.system(), platform.machine()))
    if sys.platform != "win32":
        add("!", "Windows эмас: PowerPoint, тугмалар ва флешка кузатуви бу ерда ишламайди")

    for module, required, note in (
            ("sounddevice", True, "микрофон"), ("vosk", False, "офлайн таниш"),
            ("win32com.client", False, "PowerPoint бошқаруви"),
            ("speech_recognition", False, "Google таниш")):
        try:
            importlib.import_module(module)
            add("OK", "%s юкланди (%s)" % (module, note))
        except Exception as error:
            add("X" if required else "!", "%s юкланмади (%s): %s" % (module, note, error))

    try:
        import ctypes
        import winkeys
        size = ctypes.sizeof(winkeys.INPUT)
        expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        add("OK" if size == expected else "X", "Тугмалар (SendInput) тузилмаси: %d байт" % size)
    except Exception as error:
        add("X", "winkeys: %s" % error)

    if sys.platform == "win32":
        try:
            import winreg
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "PowerPoint.Application"))
            add("OK", "PowerPoint ўрнатилган")
        except OSError:
            add("!", "PowerPoint топилмади — LibreOffice/WPS бўлса клавиатура режими ишлайди")

    try:
        audio = AudioInput(config["mic"])
        names = audio.candidates()
        import sounddevice as sd
        add("OK", "Микрофонлар: %d та (биринчиси: %s)" % (len(names), sd.query_devices(names[0])["name"]))
    except Exception as error:
        add("X", "Микрофон: %s" % error)

    root = models_root(config)
    models = discover_models(root, config["vosk_models"] or None)
    if not models:
        add("!", "Vosk моделлари топилмади (%s) — Google (онлайн) ишлатилади" % root)
    else:
        engine = None
        try:
            engine = VoskEngine(config, parser, lambda command: None, models)
            engine.load()
        except Exception as error:
            add("X", "Vosk моделлари юкланмади: %s" % error)
        if engine:
            for language, info in engine.report.items():
                add("OK", "Модель «%s» юкланди: грамматикада %d та сўз" % (language, info["grammar_words"]))
                coverage = info.get("coverage", {})
                if config["verbose"]:
                    print("      моделда йўқ сўзлар: %s" % ", ".join(info["missing"]))
                    for intent, phrases in coverage.items():
                        print("      %-14s %s" % (intent, ", ".join(phrases) or "— ЙЎҚ —"))
                weak = [intent for intent, phrases in coverage.items() if not phrases]
                if weak:
                    add("!", "«%s» моделида бу буйруқлар учун сўз йўқ: %s" % (language, ", ".join(weak)))

    found = find_presentation(root_dir(), config["preferred_names"])
    add("OK" if found else "!", "Презентация: %s" % (found or "топилмади (%s)" % root_dir()))

    failed = [text for level, text in results if level == "X"]
    print("\n%s" % ("ТАЙЁР: жиддий муаммо йўқ." if not failed else
                     "МУАММО БОР: юқоридаги [X] қаторларни ҳал қилинг."))
    return 1 if failed else 0


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Презентацияни овоз билан бошқариш")
    ap.add_argument("--file", help="презентация файли (флешкани қидирмайди)")
    ap.add_argument("--watch", action="store_true", help="флешка уланишини фонда кутиш")
    ap.add_argument("--attach", action="store_true",
                    help="PowerPoint'да аллақачон очиқ презентацияни бошқариш")
    ap.add_argument("--keys", action="store_true",
                    help="PowerPoint COM ўрнига тугмалар симуляциясидан фойдаланиш")
    ap.add_argument("--engine", choices=["auto", "vosk", "google", "both"],
                    help="таниш моторини вақтинча алмаштириш")
    ap.add_argument("--test", action="store_true",
                    help="презентациясиз: микрофон ва буйруқларни синаш")
    ap.add_argument("--selftest", action="store_true", help="компьютерни текшириш")
    ap.add_argument("--mic-test", action="store_true", help="микрофон баландлигини кўрсатиш")
    ap.add_argument("--list-mics", action="store_true", help="микрофонлар рўйхати")
    ap.add_argument("--wav", help="овоз файлини (16 кГц моно) Vosk билан таниш")
    ap.add_argument("--expect", help="--wav билан: кутилган буйруқ (next, goto:5)")
    ap.add_argument("--vocab-grep", help="модел луғатида сўз қидириш: keyin,oldin,orqa")
    ap.add_argument("--verbose", action="store_true", help="эшитилган ҳамма матнни кўрсатиш")
    args = ap.parse_args(argv)

    folder = home_dir()
    config = load_config(folder)
    if args.engine:
        config["engine"] = args.engine
    if args.test or args.verbose:
        config["verbose"] = True
    setup_logging(folder, config["log_file"])

    try:
        parser = make_parser(config)
        if args.list_mics:
            list_microphones()
            return 0
        if args.mic_test:
            return mic_test(config)
        if args.selftest:
            return selftest(config, parser)
        if args.wav:
            return wav_test(args.wav, args.expect, config, parser)
        if args.vocab_grep:
            return vocab_grep(args.vocab_grep, config)
        if args.watch:
            watch(args, config, parser)
            return 0
        if args.test or args.attach:
            return run_session(None, args, config, parser)
        if args.file:
            path = Path(args.file)
            if not path.exists():
                log.error("Файл топилмади: %s", path)
                return 1
        else:
            path = (find_presentation(root_dir(), config["preferred_names"])
                    or find_on_drives(config)
                    or wait_for_presentation(config))
        log.info("Презентация топилди: %s", path)
        return run_session(path, args, config, parser)
    except KeyboardInterrupt:
        log.info("Тўхтатилди (Ctrl+C)")
        return 0
    except (SetupError, ValueError, OSError) as error:
        log.error("%s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
