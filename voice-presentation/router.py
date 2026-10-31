# -*- coding: utf-8 -*-
"""Аниқланган буйруқни слайд ҳаракатига айлантиради."""
import time
from typing import Callable, Dict, Optional, Tuple

from commands import Command

HELP_TEXT = """\
Овозли буйруқлар (ўзбекча, русча, инглизча):
  кейингиси / keyingisi / далее / next ..... кейинги слайд
  олдингиси / oldingisi / назад / back ..... олдинги слайд
  бошидан / boshidan ....................... биринчи слайд
  охирги / oxirgi .......................... охирги слайд
  "5 слайд" / "beshinchi slayd" ............ 5-слайдга ўтиш
  қора экран / qora ekran .................. экранни қора қилиш
  оқ экран / oq ekran ...................... экранни оқ қилиш
  экранни оч / ko'rsat ..................... экранни қайтариш
  тинглама / tinglama ...................... микрофонни вақтинча ўчириш
  тингла / tingla .......................... микрофонни қайта ёқиш
  бошла / boshla ........................... намойишни қайта бошлаш
  чиқиш / chiqish .......................... намойишни ёпиш ва чиқиш"""

# Намойиш кетиб турмаса маъноси йўқ буйруқлар
_NEEDS_SHOW = {"next", "prev", "first", "last", "goto", "black", "white", "unblank"}


class CommandRouter:
    def __init__(self, backend, cooldown: float = 1.0,
                 clock: Callable[[], float] = time.monotonic):
        self.backend = backend
        self.cooldown = cooldown
        self.clock = clock
        self.listening = True
        self.should_exit = False
        self._last_seen: Dict[Tuple[str, Optional[int]], float] = {}

    def handle(self, command: Command) -> Optional[str]:
        """Буйруқни бажаради ва фойдаланувчига кўрсатиладиган хабарни қайтаради
        (None — буйруқ эътиборсиз қолдирилди)."""
        if not self.listening and command.intent != "resume_listen":
            return None

        now = self.clock()
        key = (command.intent, command.arg)
        last = self._last_seen.get(key)
        if last is not None and now - last < self.cooldown:
            return None  # бир сўз иккита таниб олиш натижасидан такрорланмасин
        self._last_seen[key] = now

        if command.intent in _NEEDS_SHOW and not self.backend.is_running():
            return "Намойиш кетмаяпти — «boshla» (бошла) денг"

        message = getattr(self, "_do_" + command.intent)(command)
        return self._with_position(message)

    # -- буйруқлар --------------------------------------------------------------
    def _do_next(self, _):
        return "Кейинги слайд" if self.backend.next() else "Бу охирги слайд"

    def _do_prev(self, _):
        return "Олдинги слайд" if self.backend.prev() else "Бу биринчи слайд"

    def _do_first(self, _):
        self.backend.first()
        return "Бошидан"

    def _do_last(self, _):
        self.backend.last()
        return "Охирги слайд"

    def _do_goto(self, command):
        if self.backend.goto(command.arg):
            return "%d-слайдга ўтилди" % command.arg
        position = self.backend.position()
        total = " (жами %d та)" % position[1] if position else ""
        return "%d-слайд йўқ%s" % (command.arg, total)

    def _do_black(self, _):
        self.backend.black()
        return "Қора экран"

    def _do_white(self, _):
        self.backend.white()
        return "Оқ экран"

    def _do_unblank(self, _):
        self.backend.unblank()
        return "Экран қайтарилди"

    def _do_pause_listen(self, _):
        self.listening = False
        return "Овозли бошқарув тўхтатилди. Қайта ёқиш учун «tingla» (тингла) денг"

    def _do_resume_listen(self, _):
        self.listening = True
        return "Овозли бошқарув қайта ёқилди"

    def _do_start(self, _):
        if self.backend.is_running():
            return "Намойиш аллақачон кетяпти"
        self.backend.start()
        return "Намойиш бошланди"

    def _do_exit(self, _):
        self.should_exit = True
        return "Намойиш ёпилмоқда"

    def _do_help(self, _):
        return HELP_TEXT

    # -- ёрдамчи ---------------------------------------------------------------
    def _with_position(self, message: str) -> str:
        if "\n" in message:
            return message
        position = self.backend.position()
        return "%s  [%d/%d]" % (message, *position) if position else message
