# -*- coding: utf-8 -*-
"""Презентацияни флешка орқали очиш ва овоз билан бошқариш.

Ишлатиш:
    python presentation_voice.py            флешкадаги презентацияни очиб, овозни тинглайди
    python presentation_voice.py --watch    фонда флешка уланишини кутади (кўп марта)
    python presentation_voice.py --test     презентациясиз, микрофон ва буйруқларни синаш
    python presentation_voice.py --list-mics   микрофонлар рўйхати
"""
import argparse
import json
import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

from backends import Backend, DryRunBackend, KeyboardBackend, PowerPointBackend
from commands import Command, CommandParser
from router import HELP_TEXT, CommandRouter

log = logging.getLogger("voicepres")

PRESENTATION_EXTENSIONS = (".pptx", ".pptm", ".ppsx", ".ppt", ".pps", ".odp")
POWERPOINT_EXTENSIONS = (".pptx", ".pptm", ".ppsx", ".ppt", ".pps")

DEFAULT_CONFIG = {
    # Google таниш тиллари. Бир нечтаси бўлса, навбати билан синалади
    # (масалан ["uz-UZ", "ru-RU"]), лекин сўровлар сони икки баравар ошади.
    "languages": ["uz-UZ"],
    "mic_index": None,                # --list-mics дан рақам; None — стандарт микрофон
    "max_words": 4,                   # шундан узун гаплар буйруқ деб ҳисобланмайди
    "max_command_seconds": 3.0,       # шундан узун овоз оқими текширилмайди (оддий нутқ)
    "phrase_time_limit": 4.0,
    "pause_threshold": 0.6,           # жимлик шунча давом этса — жумла тугади
    "cooldown_seconds": 1.0,          # бир хил буйруқ орасидаги минимал вақт
    "fuzzy_threshold": 0.85,          # 0..1; пастроқ = кўпроқ хато эшитишни кечиради
    "wake_word": "",                  # масалан "slayd": буйруқ шу сўздан бошланиши шарт
    "beep": False,                    # буйруқ қабул қилинганда сигнал
    "extra_words": {},                # масалан {"next": ["davay"]}
    "preferred_names": ["presentation", "taqdimot", "prezentatsiya"],
    "include_fixed_drives": False,    # флешка "қаттиқ диск" деб кўринса — true қилинг
    "open_delay_seconds": 5,          # заҳира (клавиатура) режимида очилишни кутиш
    "log_file": "",                   # масалан "voice_log.txt" (нима дейилгани ёзилади)
    "verbose": False,                 # true: буйруқ бўлмаган эшитилган матнни ҳам кўрсатади
}


class SetupError(Exception):
    """Фойдаланувчи ҳал қилиши мумкин бўлган созлаш хатоси (кутубхона, микрофон)."""


# ---------------------------------------------------------------------------
# Созламалар ва журнал
# ---------------------------------------------------------------------------
def app_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller .exe
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def load_config(folder: Path) -> dict:
    config = dict(DEFAULT_CONFIG)
    path = folder / "config.json"
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(user, dict):
                raise ValueError("JSON объект бўлиши керак")
            config.update(user)
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
                      and p.name.lower() != "system volume information"]
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
# Овозни тинглаш
# ---------------------------------------------------------------------------
def list_microphones() -> None:
    try:
        import speech_recognition as sr
        names = sr.Microphone.list_microphone_names()
    except (ImportError, AttributeError):
        raise SetupError("SpeechRecognition ёки PyAudio ўрнатилмаган: pip install -r requirements.txt")
    for index, name in enumerate(names):
        print("%3d  %s" % (index, name))


class VoiceListener:
    """Микрофонни тўхтовсиз тинглайди. Таниш (интернетга сўров) алоҳида оқимда
    бажарилади, шунинг учун таниш вақтида сўзлар қолиб кетмайди."""

    def __init__(self, config: dict, parser: CommandParser, results: "queue.Queue"):
        self.config = config
        self.parser = parser
        self.results = results
        self._audio: "queue.Queue" = queue.Queue(maxsize=4)
        self._stop_event = threading.Event()
        self._stop_listening = None
        self._last_net_error = 0.0
        self._continuing = False

    def prepare(self) -> None:
        """Микрофонни улаб, атроф шовқинини ўлчайди (≈1 сония жим туринг)."""
        try:
            import speech_recognition as sr
        except ImportError:
            raise SetupError("SpeechRecognition ўрнатилмаган: pip install -r requirements.txt")
        self.sr = sr
        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = self.config["pause_threshold"]
        self.recognizer.non_speaking_duration = min(0.4, self.config["pause_threshold"])
        try:
            self.microphone = sr.Microphone(device_index=self.config["mic_index"])
        except AttributeError:  # PyAudio йўқ бўлса SpeechRecognition шундай хато беради
            raise SetupError("PyAudio ўрнатилмаган: pip install PyAudio")
        except (OSError, IndexError) as error:
            raise SetupError("Микрофон топилмади (%s). Рўйхат: --list-mics" % error)
        log.info("Микрофон созланмоқда, 1 сония жим туринг...")
        with self.microphone as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=1.0)

    def begin(self) -> None:
        threading.Thread(target=self._worker, daemon=True).start()
        self._stop_listening = self.recognizer.listen_in_background(
            self.microphone, self._on_audio,
            phrase_time_limit=self.config["phrase_time_limit"])

    def stop(self) -> None:
        self._stop_event.set()
        if self._stop_listening:
            self._stop_listening(wait_for_stop=False)

    def _on_audio(self, _recognizer, audio) -> None:
        seconds = len(audio.frame_data) / float(audio.sample_rate * audio.sample_width)
        # Узун нутқ phrase_time_limit да кесилади, унинг "думи" эса қисқа бўлак бўлиб
        # келади. Бу ҳам буйруқ эмас, шунинг учун кесилган нутқдан кейинги бўлакни ҳам ташлаймиз.
        cut_off = seconds >= self.config["phrase_time_limit"] - 0.3
        skip = seconds > self.config["max_command_seconds"] or self._continuing
        self._continuing = cut_off
        if skip:
            return  # оддий нутқ — буйруқ эмас, интернетга юбормаймиз
        if self._audio.full():
            try:
                self._audio.get_nowait()  # эскисини ташлаб, энг янгисини оламиз
            except queue.Empty:
                pass
        self._audio.put_nowait(audio)

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                audio = self._audio.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._recognize(audio)
            except Exception as error:  # таниш оқими ҳеч қачон ўлмаслиги керак
                log.error("Таниш хатоси: %s", error)

    def _recognize(self, audio) -> None:
        for language in self.config["languages"]:
            try:
                result = self.recognizer.recognize_google(
                    audio, language=language, show_all=True)
            except self.sr.UnknownValueError:
                continue  # шовқин ёки тушунарсиз товуш (янги версияларда show_all билан ҳам шу)
            except self.sr.RequestError:
                if time.monotonic() - self._last_net_error > 30:
                    log.warning("Интернет билан алоқа йўқ — овоз танилмаяпти")
                    self._last_net_error = time.monotonic()
                return
            alternatives = result.get("alternative", []) if isinstance(result, dict) else []
            texts = [a["transcript"] for a in alternatives if a.get("transcript")]
            if not texts:
                continue
            command = self.parser.parse(texts)
            if command:
                log.info("Эшитилди: «%s»", texts[0])
                self.results.put(command)
                return
            if self.config["verbose"]:
                log.info("Эшитилди (буйруқ эмас): «%s»", texts[0])


# ---------------------------------------------------------------------------
# Сессия
# ---------------------------------------------------------------------------
def make_parser(config: dict) -> CommandParser:
    return CommandParser(
        threshold=config["fuzzy_threshold"],
        max_words=config["max_words"],
        wake_word=config["wake_word"],
        extra_words=config["extra_words"])


def open_backend(path: Optional[Path], args, config: dict) -> Backend:
    """Презентацияни очиб, шоуни бошлайди. PowerPoint ишламаса — клавиатура режими."""
    if args.test:
        backend = DryRunBackend()
        backend.start()
        return backend

    if (not args.keys and sys.platform == "win32"
            and (path is None or path.suffix.lower() in POWERPOINT_EXTENSIONS)):
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
        except Exception as error:
            log.warning("PowerPoint COM ишламади (%s) — клавиатура режимига ўтилди", error)

    backend = KeyboardBackend(open_delay=config["open_delay_seconds"])
    if path is not None:
        backend.open(path)
    backend.start()
    log.info("Клавиатура режими: презентация ойнаси фаол бўлиб туриши керак")
    return backend


def beep() -> None:
    try:
        import winsound
        winsound.Beep(1000, 60)
    except Exception:
        pass


def run_session(path: Optional[Path], args, config: dict, parser: CommandParser) -> None:
    results: "queue.Queue" = queue.Queue()
    listener = VoiceListener(config, parser, results)
    listener.prepare()  # микрофон хатоси презентация очилишидан олдин маълум бўлсин

    backend = open_backend(path, args, config)
    router = CommandRouter(backend, cooldown=config["cooldown_seconds"])
    started = time.monotonic()
    listener.begin()
    log.info("Овозли бошқарув ишга тушди. «yordam» (ёрдам) десангиз буйруқлар чиқади.")
    if args.test:
        log.info("\n%s", HELP_TEXT)

    try:
        while not router.should_exit:
            try:
                command: Command = results.get(timeout=0.5)
            except queue.Empty:
                if path is not None and not path.exists():
                    log.warning("Флешка узилди — тугатилмоқда")
                    break
                continue
            message = router.handle(command)
            if message:
                log.info("%s", message)
                if config["beep"]:
                    beep()
    finally:
        listener.stop()
        backend.close()
        minutes, seconds = divmod(int(time.monotonic() - started), 60)
        log.info("Намойиш давомийлиги: %02d:%02d", minutes, seconds)


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
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Презентацияни овоз билан бошқариш")
    parser.add_argument("--file", help="презентация файли (флешкани қидирмайди)")
    parser.add_argument("--watch", action="store_true", help="флешка уланишини фонда кутиш")
    parser.add_argument("--attach", action="store_true",
                        help="PowerPoint'да аллақачон очиқ турган презентацияни бошқариш")
    parser.add_argument("--keys", action="store_true",
                        help="PowerPoint COM ўрнига тугмалар симуляциясидан фойдаланиш")
    parser.add_argument("--test", action="store_true",
                        help="презентациясиз: микрофон ва буйруқларни синаш")
    parser.add_argument("--list-mics", action="store_true", help="микрофонлар рўйхати")
    args = parser.parse_args(argv)

    folder = app_dir()
    config = load_config(folder)
    if args.test:
        config["verbose"] = True
    setup_logging(folder, config["log_file"])

    try:
        if args.list_mics:
            list_microphones()
            return 0
        parser_ = make_parser(config)
        if args.watch:
            watch(args, config, parser_)
        elif args.test or args.attach:
            run_session(None, args, config, parser_)
        else:
            if args.file:
                path = Path(args.file)
                if not path.exists():
                    log.error("Файл топилмади: %s", path)
                    return 1
            else:
                path = (find_presentation(folder, config["preferred_names"])
                        or find_on_drives(config)
                        or wait_for_presentation(config))
            log.info("Презентация топилди: %s", path)
            run_session(path, args, config, parser_)
    except KeyboardInterrupt:
        log.info("Тўхтатилди (Ctrl+C)")
    except (SetupError, ValueError, OSError) as error:
        log.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
