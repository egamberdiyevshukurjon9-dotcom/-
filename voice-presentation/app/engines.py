# -*- coding: utf-8 -*-
"""Овозни таниш моторлари.

  * VoskEngine   — офлайн (интернетсиз). Моделлар `models/uz`, `models/ru`, `models/en`
    папкаларида туради. Грамматика режимида модель фақат буйруқ сўзларини қидиради:
    бу тезроқ ва аниқроқ, оддий нутқни эса "[unk]" деб ҳисоблайди.
  * GoogleEngine — онлайн (Google). Интернет керак, лекин ўзбек тилини яхши билади.

Иккаласи ҳам микрофон оқимидан (audio.AudioInput) бўлакларни олади ва топилган
буйруқни `emit(Command)` орқали беради. Буйруқ овоз айтилган вақт белгиси билан
келади — шу туфайли икки мотор бирга ишлаганда ҳам бир сўз икки марта бажарилмайди.
"""
import importlib.util
import json
import logging
import queue
import sys
import threading
import wave
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from audio import BLOCK_FRAMES, RATE, Segmenter
from commands import Command, CommandParser, norm
from errors import EngineError

log = logging.getLogger("voicepres")

Emit = Callable[[Command], None]


# ---------------------------------------------------------------------------
# Моделларни топиш ва луғат билан ишлаш
# ---------------------------------------------------------------------------
def is_model_dir(path: Path) -> bool:
    return (path / "am" / "final.mdl").exists() or (path / "conf" / "model.conf").exists()


def guess_language(name: str) -> str:
    parts = [p for p in name.lower().replace(".", "-").replace("_", "-").split("-") if p]
    for part in parts:
        if len(part) == 2 and part.isalpha():
            return part
    return name.lower()


def discover_models(root: Path, wanted: Optional[List[str]] = None) -> Dict[str, Path]:
    """`models/` папкасидаги Vosk моделлари: {"uz": Path, "ru": Path, "en": Path}."""
    found: Dict[str, Path] = {}
    if not root or not root.is_dir():
        return found
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        model = directory
        if not is_model_dir(model):  # zip ичидаги битта қўшимча папка
            inner = [p for p in directory.iterdir() if p.is_dir() and is_model_dir(p)]
            if not inner:
                continue
            model = inner[0]
        language = guess_language(directory.name)
        if wanted and language not in wanted:
            continue
        found.setdefault(language, model)
    return found


def read_vocabulary(model_dir: Path) -> Optional[Dict[str, List[str]]]:
    """Модель луғати: {меъёрлаштирилган сўз: [моделдаги ёзилиши, ...]}."""
    for relative in ("graph/words.txt", "words.txt"):
        path = model_dir / relative
        if not path.exists():
            continue
        mapping: Dict[str, List[str]] = {}
        with open(path, encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                word = line.split(" ", 1)[0].strip()
                if not word or len(word) > 24 or word[0] in "<#[":
                    continue
                key = norm(word)
                if key and " " not in key:
                    mapping.setdefault(key, []).append(word)
        return mapping
    return None


def build_grammar(parser: CommandParser, vocabulary: Dict[str, List[str]]
                  ) -> Tuple[List[str], List[str]]:
    """Грамматика сўзлари (моделда борлари) ва моделда топилмаган сўзлар."""
    needed = parser.vocabulary()
    words = sorted({raw for token in needed for raw in vocabulary.get(token, [])})
    missing = sorted(t for t in needed if t not in vocabulary)
    return words, missing


def ascii_safe_path(path: Path) -> str:
    """Vosk (C++) кирилл ёзувли йўлни очолмайди — Windows'да қисқа (8.3) ном олинади."""
    text = str(path)
    if text.isascii() or sys.platform != "win32":
        return text
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetShortPathNameW(text, buffer, len(buffer)) and buffer.value.isascii():
            return buffer.value
    except Exception:
        pass
    log.warning("Модель йўлида лотин бўлмаган ҳарфлар бор: %s. Агар модель "
                "юкланмаса, лотин ҳарфли папкага кўчиринг.", text)
    return text


def texts_from_result(raw: str) -> List[str]:
    data = json.loads(raw)
    if data.get("alternatives"):
        texts = [a.get("text", "") for a in data["alternatives"]]
    else:
        texts = [data.get("text", "")]
    return [t.replace("[unk]", "unk") for t in texts if t.strip()]


def read_wav_pcm(path) -> bytes:
    with wave.open(str(path), "rb") as handle:
        if (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) != (RATE, 1, 2):
            raise EngineError("WAV 16 кГц, моно, 16 бит бўлиши керак (ҳозир: %d Гц, %d канал, %d бит)"
                              % (handle.getframerate(), handle.getnchannels(), handle.getsampwidth() * 8))
        return handle.readframes(handle.getnframes())


# ---------------------------------------------------------------------------
class VoskEngine:
    name = "Vosk (офлайн)"

    def __init__(self, config: dict, parser: CommandParser, emit: Emit,
                 models: Dict[str, Path], vosk_module=None):
        self.config, self.parser, self.emit = config, parser, emit
        self.models = models
        self._vosk = vosk_module
        self.recognizers: Dict[str, object] = {}
        self.report: Dict[str, dict] = {}
        self._stop = threading.Event()

    def load(self) -> None:
        if self._vosk is None:
            try:
                import vosk
            except (ImportError, OSError) as error:
                raise EngineError("Vosk кутубхонаси юкланмади: %s" % error)
            self._vosk = vosk
        vosk = self._vosk
        try:
            vosk.SetLogLevel(-1)
        except Exception:
            pass
        for language, path in self.models.items():
            try:
                model = vosk.Model(ascii_safe_path(path))
            except Exception as error:
                raise EngineError("Vosk модели юкланмади (%s): %s" % (path, error))
            grammar = None
            info = {"path": str(path), "grammar_words": 0, "missing": []}
            if self.config.get("vosk_grammar", True):
                vocabulary = read_vocabulary(path)
                if vocabulary:
                    words, info["missing"] = build_grammar(self.parser, vocabulary)
                    info["grammar_words"] = len(words)
                    info["coverage"] = self.parser.coverage(set(vocabulary))
                    if words:
                        grammar = json.dumps(words + ["[unk]"], ensure_ascii=False)
            recognizer = (vosk.KaldiRecognizer(model, RATE, grammar) if grammar
                          else vosk.KaldiRecognizer(model, RATE))
            try:
                recognizer.SetMaxAlternatives(int(self.config.get("vosk_alternatives", 3)))
            except Exception:
                pass
            self.recognizers[language] = recognizer
            self.report[language] = info

    # -- оқим ---------------------------------------------------------------------
    def start(self, audio_queue: "queue.Queue") -> None:
        threading.Thread(target=self._loop, args=(audio_queue,), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self, audio_queue: "queue.Queue") -> None:
        while not self._stop.is_set():
            try:
                stamp, block = audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.feed(block, stamp)
            except Exception as error:
                log.error("Vosk хатоси: %s", error)

    def feed(self, block: bytes, stamp: float) -> None:
        for language, recognizer in self.recognizers.items():
            if recognizer.AcceptWaveform(block):
                self._deliver(language, texts_from_result(recognizer.Result()), stamp)

    def _deliver(self, language: str, texts: List[str], stamp: float) -> None:
        if not texts:
            return
        command = self.parser.parse(texts)
        if command:
            log.info("Эшитилди [%s]: «%s»", language, texts[0])
            self.emit(replace(command, at=stamp))
        elif self.config.get("verbose"):
            log.info("Эшитилди [%s] (буйруқ эмас): «%s»", language, texts[0])

    # -- файл орқали синаш ---------------------------------------------------------
    def process_pcm(self, pcm: bytes) -> List[Tuple[str, List[str], Optional[Command]]]:
        """Тайёр овозни (16 кГц моно PCM) тўлиқ ишлаб чиқади. Синов учун."""
        results: List[Tuple[str, List[str], Optional[Command]]] = []
        original = self.emit
        self.emit = lambda command: None
        original_deliver = self._deliver

        def collect(language, texts, stamp):
            results.append((language, texts, self.parser.parse(texts) if texts else None))

        self._deliver = collect  # type: ignore[assignment]
        try:
            pcm = pcm + b"\x00" * (RATE * 2)  # охирида 1 сония жимлик: жумла тугаши учун
            step = BLOCK_FRAMES * 2
            for offset in range(0, len(pcm), step):
                self.feed(pcm[offset:offset + step], 0.0)
            for language, recognizer in self.recognizers.items():
                texts = texts_from_result(recognizer.FinalResult())
                if texts:
                    collect(language, texts, 0.0)
        finally:
            self.emit = original
            self._deliver = original_deliver  # type: ignore[assignment]
        return results


# ---------------------------------------------------------------------------
class GoogleEngine:
    name = "Google (онлайн)"

    def __init__(self, config: dict, parser: CommandParser, emit: Emit, sr_module=None):
        self.config, self.parser, self.emit = config, parser, emit
        self.sr = sr_module
        self.recognizer = None
        self.segmenter = Segmenter(
            pause=config["pause_threshold"], max_phrase=config["phrase_time_limit"],
            min_energy=config["min_energy"])
        self._segments: "queue.Queue" = queue.Queue(maxsize=4)
        self._stop = threading.Event()
        self._continuing = False
        self._last_net_error = 0.0

    def load(self) -> None:
        if self.sr is None:
            try:
                import speech_recognition as sr
            except ImportError as error:
                raise EngineError("SpeechRecognition кутубхонаси юкланмади: %s" % error)
            self.sr = sr
        self.recognizer = self.sr.Recognizer()

    def start(self, audio_queue: "queue.Queue") -> None:
        threading.Thread(target=self._segment_loop, args=(audio_queue,), daemon=True).start()
        threading.Thread(target=self._worker, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _segment_loop(self, audio_queue: "queue.Queue") -> None:
        while not self._stop.is_set():
            try:
                stamp, block = audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            segment = self.segmenter.feed(block, stamp)
            if segment:
                self.on_segment(segment)

    def on_segment(self, segment) -> None:
        # Узун нутқ phrase_time_limit да кесилади, унинг "думи" эса қисқа бўлак бўлиб келади.
        # Иккаласи ҳам буйруқ эмас — интернетга юбормаймиз.
        skip = segment.seconds > self.config["max_command_seconds"] or self._continuing
        self._continuing = segment.cut
        if skip:
            return
        if self._segments.full():
            try:
                self._segments.get_nowait()
            except queue.Empty:
                pass
        self._segments.put_nowait(segment)

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                segment = self._segments.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.recognize(segment)
            except Exception as error:
                log.error("Google таниш хатоси: %s", error)

    def recognize(self, segment) -> None:
        audio = self.sr.AudioData(segment.audio, RATE, 2)
        for language in self.config["languages"]:
            try:
                result = self.recognizer.recognize_google(audio, language=language, show_all=True)
            except self.sr.UnknownValueError:
                continue  # шовқин ёки тушунарсиз товуш
            except self.sr.RequestError:
                import time
                if time.monotonic() - self._last_net_error > 30:
                    log.warning("Интернет билан алоқа йўқ — Google овозни танимаяпти")
                    self._last_net_error = time.monotonic()
                return
            alternatives = result.get("alternative", []) if isinstance(result, dict) else []
            texts = [a["transcript"] for a in alternatives if a.get("transcript")]
            if not texts:
                continue
            command = self.parser.parse(texts)
            if command:
                log.info("Эшитилди [google %s]: «%s»", language, texts[0])
                self.emit(replace(command, at=segment.stamp))
                return
            if self.config.get("verbose"):
                log.info("Эшитилди [google] (буйруқ эмас): «%s»", texts[0])


# ---------------------------------------------------------------------------
def build_engines(config: dict, parser: CommandParser, emit: Emit, models_root: Path
                  ) -> List[object]:
    """Созламадаги `engine` бўйича моторларни танлайди (юкламасдан)."""
    mode = config.get("engine", "auto")
    if mode not in ("auto", "vosk", "google", "both"):
        raise EngineError("config.json: engine = auto | vosk | google | both (ҳозир: %r)" % mode)
    models = discover_models(models_root, config.get("vosk_models") or None)
    vosk_ready = bool(models) and importlib.util.find_spec("vosk") is not None

    if mode in ("vosk", "both") and not vosk_ready:
        if mode == "vosk":
            raise EngineError(
                "Vosk модели топилмади (%s) ёки vosk кутубхонаси йўқ. "
                "Моделларни models/ папкасига қўйинг ёки config.json да engine=google қилинг."
                % models_root)
        log.warning("Vosk модели топилмади — фақат Google ишлатилади")

    engines: List[object] = []
    if mode in ("auto", "vosk", "both") and vosk_ready:
        engines.append(VoskEngine(config, parser, emit, models))
    if mode in ("google", "both") or (mode == "auto" and not engines):
        engines.append(GoogleEngine(config, parser, emit))
    return engines


def load_engines(config: dict, parser: CommandParser, emit: Emit, models_root: Path) -> List[object]:
    """Моторларни танлаб юклайди. `auto` режимида Vosk юкланмаса, Google'га ўтилади."""
    engines = build_engines(config, parser, emit, models_root)
    loaded: List[object] = []
    for engine in engines:
        try:
            engine.load()
            loaded.append(engine)
        except EngineError as error:
            if config.get("engine", "auto") == "auto" and isinstance(engine, VoskEngine):
                log.warning("%s — Google'га ўтилмоқда", error)
                google = GoogleEngine(config, parser, emit)
                google.load()
                loaded.append(google)
            else:
                raise
    return loaded
