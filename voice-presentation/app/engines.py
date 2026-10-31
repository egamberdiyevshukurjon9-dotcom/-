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
import hashlib
import importlib.util
import json
import logging
import os
import queue
import re
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
    if wanted:  # устувор тартиб: config.json даги рўйхат бўйича (биринчиси - асосий тил)
        found = {lang: found[lang] for lang in wanted if lang in found}
    return found


def find_known_words(model, candidates) -> List[str]:
    """Моделнинг ўз луғатида борлиги текширилган сўзлар (луғат файли керак эмас)."""
    return sorted(w for w in candidates if model.vosk_model_find_word(w) > 0)


def _short_path(text: str) -> Optional[str]:
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetShortPathNameW(text, buffer, len(buffer)):
            return buffer.value
    except Exception:
        pass
    return None


def _junction(path: Path) -> Optional[str]:
    """ProgramData\\VoicePresentation\\links да лотин ёзувли ҳавола (администратор керак эмас)."""
    try:
        import _winapi
        base = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "VoicePresentation" / "links"
        base.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(str(path).lower().encode("utf-8")).hexdigest()[:12]
        link = base / ("%s-%s" % (key, re.sub(r"[^A-Za-z0-9._-]", "_", path.name)))
        if os.path.lexists(link) and not link.exists():
            os.rmdir(link)  # нишони йўқолган эски ҳавола
        if not os.path.lexists(link):
            _winapi.CreateJunction(str(path), str(link))
        return str(link)
    except Exception as error:
        log.debug("junction ясалмади: %s", error)
        return None


def ascii_safe_path(path: Path) -> str:
    """Vosk (C++) кирил ёзувли йўлни очолмайди. Windows'да аввал қисқа (8.3) ном, у бўлмаса
    ProgramData'даги лотин ёзувли ҳавола (junction) ишлатилади."""
    text = str(path)
    if text.isascii() or sys.platform != "win32":
        return text
    short = _short_path(text)
    if short and short.isascii():
        return short
    link = _junction(path)
    if link and link.isascii():
        return link
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
        self.on_heard: Optional[Callable[[List[str], Optional[Command]], None]] = None

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
        mode = self.config.get("vosk_grammar", "primary")
        for index, (language, path) in enumerate(self.models.items()):
            # Грамматика фақат асосий (биринчи) тил учун: бошқа тилдаги модель грамматика билан
            # оддий нутқни тасодифан буйруқ сўзига "мажбурлаб" келтиради (CI да рус модели
            # инглизча гапни "слушай" деб эшитган). Қолган тиллар эркин таниш режимида.
            use_grammar = mode in (True, "all") or (mode == "primary" and index == 0)
            try:
                model = vosk.Model(ascii_safe_path(path))
            except Exception as error:
                raise EngineError("Vosk модели юкланмади (%s): %s" % (path, error))
            grammar = None
            known = find_known_words(model, self.parser.candidate_words())
            available = {norm(w) for w in known}
            info = {"path": str(path), "grammar_words": len(known), "grammar": use_grammar,
                    "missing": sorted(self.parser.vocabulary() - available),
                    "coverage": self.parser.coverage(available)}
            if use_grammar:
                if known:
                    grammar = json.dumps(known + ["[unk]"], ensure_ascii=False)
                else:
                    log.warning("«%s» моделида буйруқ сўзлари топилмади — грамматикасиз ишлайди", language)
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

    def _decode(self, block: bytes) -> List[Tuple[str, List[str], Optional[Command]]]:
        """Бўлакни ҳамма моделларга беради; жумла тугаган моделлардан натижа олади."""
        outcomes = []
        for language, recognizer in self.recognizers.items():
            if recognizer.AcceptWaveform(block):
                texts = texts_from_result(recognizer.Result())
                if texts:
                    outcomes.append((language, texts, self.parser.parse(texts)))
        return outcomes

    def feed(self, block: bytes, stamp: float) -> None:
        # Битта овозга бир нечта модель турлича буйруқ қайтариши мумкин (масалан, рус модели
        # ўзбекча сўзни "назад" деб эшитса). Фақат энг устувор модельники (config тартиби) олинади.
        chosen = None
        outcomes = self._decode(block)
        if outcomes and self.on_heard:  # ҳисобот: нечта жумла эшитилди, нечтаси буйруқ бўлди
            first = next((o for o in outcomes if o[2]), outcomes[0])
            self.on_heard(first[1], first[2])
        for language, texts, command in outcomes:
            if command and chosen is None:
                chosen = command
                log.info("Эшитилди [%s]: «%s»", language, texts[0])
            elif self.config.get("verbose"):
                log.info("Эшитилди [%s] (%s): «%s»", language,
                         "иккинчи даражали" if command else "буйруқ эмас", texts[0])
        if chosen is not None:
            self.emit(replace(chosen, at=stamp))

    # -- файл орқали синаш ---------------------------------------------------------
    def process_pcm(self, pcm: bytes) -> List[Tuple[str, List[str], Optional[Command]]]:
        """Тайёр овозни (16 кГц моно PCM) тўлиқ ишлаб чиқади. Синов учун."""
        results: List[Tuple[str, List[str], Optional[Command]]] = []
        pcm = pcm + b"\x00" * (RATE * 2)  # охирида 1 сония жимлик: жумла тугаши учун
        step = BLOCK_FRAMES * 2
        for offset in range(0, len(pcm), step):
            results.extend(self._decode(pcm[offset:offset + step]))
        for language, recognizer in self.recognizers.items():
            texts = texts_from_result(recognizer.FinalResult())
            if texts:
                results.append((language, texts, self.parser.parse(texts)))
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
        self.on_heard: Optional[Callable[[List[str], Optional[Command]], None]] = None

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

    def transcribe(self, audio_bytes: bytes):
        """Google'дан матн вариантларини олади: [(тил, [матн, ...]), ...]. Интернет йўқ бўлса — None."""
        audio = self.sr.AudioData(audio_bytes, RATE, 2)
        heard = []
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
                return None
            alternatives = result.get("alternative", []) if isinstance(result, dict) else []
            texts = [a["transcript"] for a in alternatives if a.get("transcript")]
            if texts:
                heard.append((language, texts))
        return heard

    def recognize(self, segment) -> None:
        heard = self.transcribe(segment.audio)
        for language, texts in heard or []:
            command = self.parser.parse(texts)
            if command:
                log.info("Эшитилди [google %s]: «%s»", language, texts[0])
                if self.on_heard:
                    self.on_heard(texts, command)
                self.emit(replace(command, at=segment.stamp))
                return
        if heard:
            if self.on_heard:
                self.on_heard(heard[0][1], None)
            if self.config.get("verbose"):
                log.info("Эшитилди [google] (буйруқ эмас): «%s»", heard[0][1][0])


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
