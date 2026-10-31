# -*- coding: utf-8 -*-
"""Микрофон: тўхтовсиз ўқиш, узилса қайта улаш, овозни жумлаларга ажратиш.

Овоз 16 кГц, моно, 16 бит форматда 0,1 сониялик бўлакларда (block) тарқатилади.
Ҳар бир бўлак вақт белгиси (time.monotonic) билан келади — шу орқали икки хил
таниш моторидан келган бир хил буйруқ битта деб ҳисобланади.
"""
import array
import logging
import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, List, Optional

from errors import AudioError

log = logging.getLogger("voicepres")

RATE = 16000
BLOCK_SECONDS = 0.1
BLOCK_FRAMES = int(RATE * BLOCK_SECONDS)
SILENCE_WARNING_BLOCKS = 40  # 4 сония тўлиқ жимлик


def rms(block: bytes) -> float:
    """16-бит PCM бўлакнинг ўртача квадратик қиймати (овоз баландлиги)."""
    samples = array.array("h")
    samples.frombytes(block[: len(block) // 2 * 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


# ---------------------------------------------------------------------------
class AudioInput:
    """Микрофонни ўқиб, бўлакларни обуначиларга тарқатади."""

    def __init__(self, preference=None, sd_module=None,
                 status: Optional[Callable[[str], None]] = None):
        self.preference = preference   # None | индекс | номнинг бир қисми
        self._sd = sd_module
        self._status = status or log.warning
        self._subscribers: List["queue.Queue"] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stream = None
        self.device_name = ""
        self._zero_run = 0
        self._silence_reported = False
        self._last_report = 0.0

    # -- ташқи интерфейс ---------------------------------------------------------
    def subscribe(self, maxsize: int = 100) -> "queue.Queue":
        q: "queue.Queue" = queue.Queue(maxsize=maxsize)
        self._subscribers.append(q)
        return q

    def start(self) -> str:
        """Микрофонни очади (хато бўлса AudioError) ва ўқишни бошлайди."""
        self._stream = self._open()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self.device_name

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._close()

    # -- қурилмаларни танлаш ---------------------------------------------------------
    def _sounddevice(self):
        if self._sd is None:
            try:
                import sounddevice
            except (ImportError, OSError) as error:
                raise AudioError("sounddevice (PortAudio) юкланмади: %s" % error)
            self._sd = sounddevice
        return self._sd

    def candidates(self) -> list:
        """Синаб кўриладиган қурилмалар: аввал танланган, кейин стандарт, кейин қолганлари."""
        sd = self._sounddevice()
        try:
            devices = sd.query_devices()
        except Exception as error:
            raise AudioError("Қурилмалар рўйхатини олиб бўлмади: %s" % error)
        inputs = [(i, d) for i, d in enumerate(devices) if d.get("max_input_channels", 0) > 0]
        if not inputs:
            raise AudioError("Микрофон топилмади. Микрофон ёки гарнитурани улаб, "
                             "Windows'да ёқилганини текширинг.")
        order: List[int] = []
        pref = self.preference
        if isinstance(pref, int):
            order.append(pref)
        elif isinstance(pref, str) and pref.strip():
            order += [i for i, d in inputs if pref.lower() in d["name"].lower()]
        try:
            default_in = sd.default.device[0]
            if isinstance(default_in, int) and default_in >= 0:
                order.append(default_in)
        except Exception:
            pass
        order += [i for i, _ in inputs]
        seen, result = set(), []
        for index in order:
            if index not in seen and any(index == i for i, _ in inputs):
                seen.add(index)
                result.append(index)
        return result

    def _open(self):
        sd = self._sounddevice()
        errors = []
        for index in self.candidates():
            try:
                stream = sd.RawInputStream(samplerate=RATE, blocksize=BLOCK_FRAMES,
                                           dtype="int16", channels=1, device=index)
                stream.start()
                self.device_name = sd.query_devices(index)["name"]
                return stream
            except Exception as error:
                errors.append("[%s] %s" % (index, error))
        raise AudioError("Ҳеч бир микрофон очилмади: " + "; ".join(errors[:3]))

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    # -- ўқиш цикли ---------------------------------------------------------------
    def _report(self, text: str, every: float = 30.0) -> None:
        if time.monotonic() - self._last_report > every:
            self._last_report = time.monotonic()
            self._status(text)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._stream is None:
                try:
                    self._stream = self._open()
                    self._status("Микрофон уланди: %s" % self.device_name)
                except AudioError as error:
                    self._report("Микрофонга уланиб бўлмади, қайта уринилмоқда... (%s)" % error)
                    self._stop.wait(2.0)
                    continue
            try:
                data, _overflow = self._stream.read(BLOCK_FRAMES)
            except Exception as error:
                self._close()
                self._report("Микрофон узилди, қайта уланилмоқда... (%s)" % error, every=5.0)
                self._stop.wait(1.0)
                continue
            self._publish(time.monotonic(), bytes(data))

    def _publish(self, stamp: float, block: bytes) -> None:
        self._check_silence(block)
        for q in self._subscribers:
            if q.full():
                try:
                    q.get_nowait()  # эскисини ташлаб, энг янгисини қолдирамиз
                except queue.Empty:
                    pass
            q.put_nowait((stamp, block))

    def _check_silence(self, block: bytes) -> None:
        if block.count(0) == len(block):
            self._zero_run += 1
            if self._zero_run >= SILENCE_WARNING_BLOCKS and not self._silence_reported:
                self._silence_reported = True
                self._status(
                    "Микрофондан умуман овоз келмаяпти. Windows → Созламалар → Махфийлик → "
                    "Микрофон: «Иловаларга микрофондан фойдаланишга рухсат бериш»ни ёқинг, "
                    "ёки микрофон ўчиб қолмаганини текширинг.")
        else:
            self._zero_run = 0


# ---------------------------------------------------------------------------
@dataclass
class Segment:
    audio: bytes
    seconds: float
    cut: bool          # чекланган вақтда кесилди (давомли нутқ)
    stamp: float = 0.0


class Segmenter:
    """Энергия бўйича овозни жумлаларга ажратади (Google мотори учун)."""

    def __init__(self, pause: float = 0.6, max_phrase: float = 4.0,
                 min_speech: float = 0.2, start_ratio: float = 3.0,
                 min_energy: float = 300.0, calibration_blocks: int = 8):
        self.pause_blocks = max(1, round(pause / BLOCK_SECONDS))
        self.max_blocks = round(max_phrase / BLOCK_SECONDS)
        self.min_speech_blocks = max(1, round(min_speech / BLOCK_SECONDS))
        self.start_ratio = start_ratio
        self.min_energy = min_energy
        self.calibration_blocks = calibration_blocks
        self._calibration: List[float] = []
        self.noise = 0.0
        self._pre: deque = deque(maxlen=3)
        self._reset()

    def _reset(self) -> None:
        self._buf: List[bytes] = []
        self._active = False
        self._silent = 0
        self._speech = 0

    @property
    def threshold(self) -> float:
        return max(self.min_energy, self.noise * self.start_ratio)

    def feed(self, block: bytes, stamp: float = 0.0) -> Optional[Segment]:
        level = rms(block)
        if len(self._calibration) < self.calibration_blocks:
            self._calibration.append(level)
            self.noise = sum(self._calibration) / len(self._calibration)
            return None
        loud = level > self.threshold
        if not self._active:
            if loud:
                self._active = True
                self._buf = list(self._pre) + [block]
                self._speech = 1
                self._silent = 0
            else:
                self._pre.append(block)
                self.noise = 0.9 * self.noise + 0.1 * level  # шовқин даражасига мослашиш
            return None

        self._buf.append(block)
        if loud:
            self._silent = 0
            self._speech += 1
        else:
            self._silent += 1
        if self._silent >= self.pause_blocks:
            return self._finish(cut=False, stamp=stamp)
        if len(self._buf) >= self.max_blocks:
            return self._finish(cut=True, stamp=stamp)
        return None

    def _finish(self, cut: bool, stamp: float) -> Optional[Segment]:
        enough = self._speech >= self.min_speech_blocks
        audio = b"".join(self._buf)
        self._pre.clear()
        self._reset()
        if not enough:
            return None  # қисқа шовқин
        return Segment(audio, len(audio) / (RATE * 2.0), cut, stamp)
