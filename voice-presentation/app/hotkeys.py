# -*- coding: utf-8 -*-
"""Push-to-talk: овозли буйруқлар фақат белгиланган тугма босиб турилганда қабул қилинади.

Нутқ пайтида тасодифий ишга тушишнинг энг ишончли ҳимояси: тугма босилмаса, дастур
нима эшитса ҳам слайд ўтмайди. Тугма ҳолати GetAsyncKeyState орқали ўқилади, шунинг учун
консоль ойнаси фаол бўлиши шарт эмас.
"""
import logging
import sys
import threading
import time
from collections import deque
from typing import Callable, Deque, Optional, Tuple

log = logging.getLogger("voicepres")

VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "space": 0x20, "tab": 0x09, "capslock": 0x14,
      "scrolllock": 0x91, "pause": 0x13, "insert": 0x2D, "rctrl": 0xA3, "rshift": 0xA1, "ralt": 0xA5}
VK.update({"f%d" % n: 0x6F + n for n in range(1, 13)})  # F1..F12 = 0x70..0x7B


def key_code(name: str) -> int:
    key = name.strip().lower()
    if key not in VK:
        raise ValueError("Номаълум тугма «%s». Мумкин: %s" % (name, ", ".join(sorted(VK))))
    return VK[key]


def windows_key_state(vk: int) -> bool:
    import ctypes
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


class PushToTalk:
    def __init__(self, key: str, key_state: Optional[Callable[[int], bool]] = None,
                 clock: Callable[[], float] = time.monotonic, grace: float = 1.5,
                 lead: float = 0.4, poll: float = 0.03):
        self.vk = key_code(key)
        self.key = key
        if key_state is None:
            if sys.platform != "win32":
                raise OSError("Push-to-talk фақат Windows'да ишлайди")
            key_state = windows_key_state
        self.key_state = key_state
        self.clock = clock
        self.grace = grace      # тугма қўйиб юборилгандан кейин таниш тугашини кутиш (Vosk ~0,6 с кечикади)
        self.lead = lead
        self.poll = poll
        self._down_since: Optional[float] = None
        self._intervals: Deque[Tuple[float, float]] = deque(maxlen=200)
        self._stop = threading.Event()

    def poll_once(self) -> None:
        now = self.clock()
        down = self.key_state(self.vk)
        if down and self._down_since is None:
            self._down_since = now
        elif not down and self._down_since is not None:
            self._intervals.append((self._down_since, now))
            self._down_since = None

    def allows(self, moment: float) -> bool:
        """Шу пайтда айтилган овозга рухсатми? (moment — овоз вақти, time.monotonic)"""
        if self._down_since is not None and moment >= self._down_since - self.lead:
            return True
        return any(start - self.lead <= moment <= end + self.grace for start, end in self._intervals)

    def start(self) -> None:
        def run() -> None:
            while not self._stop.is_set():
                try:
                    self.poll_once()
                except Exception as error:
                    log.error("Push-to-talk хатоси: %s", error)
                    return
                self._stop.wait(self.poll)
        threading.Thread(target=run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
