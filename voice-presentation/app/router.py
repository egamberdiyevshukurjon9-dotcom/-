# -*- coding: utf-8 -*-
"""Аниқланган буйруқни слайд ҳаракатига айлантиради."""
import time
from typing import Callable, Dict, List, Optional, Tuple

from commands import Command

HELP_TEXT = """\
Овозли буйруқлар (ўзбекча, русча, инглизча):
  кейингиси / keyingisi / далее / next ..... кейинги слайд
  олдингиси / oldingisi / назад / back ..... олдинги слайд
  бошидан / boshidan ....................... биринчи слайд
  охирги / oxirgi .......................... охирги слайд
  "5 слайд" / "beshinchi slayd" ............ 5-слайдга ўтиш
  "2 слайд олдинга" / "3 slayd orqaga" .... нисбий ўтиш
  қора экран / qora ekran .................. экранни қора қилиш
  оқ экран / oq ekran ...................... экранни оқ қилиш
  экранни оч / ko'rsat ..................... экранни қайтариш
  автоматик / "avtomatik 20 soniya" ........ ҳар N сонияда ўзи ўтади
  қўлда / qo'lda ........................... автоматик ўтишни ўчиради
  ҳолат / vaqt ............................. слайд рақами ва вақт
  белгила / belgila → белгига қайт ......... слайдни эслаб қолиш ва унга қайтиш (Q&A дан кейин)
  ҳисобот / hisobot ........................ слайд вақтлари ва буйруқлар статистикаси
  изоҳ / izoh .............................. слайд изоҳини чиқаради
  мундарижа / mundarija .................... слайдлар рўйхати
  тинглама / tinglama ...................... микрофонни вақтинча ўчириш
  тингла / tingla .......................... микрофонни қайта ёқиш
  бошла / boshla ........................... намойишни қайта бошлаш
  чиқиш / chiqish → ҳа / ha ................ намойишни ёпиш ва чиқиш (тасдиқ билан)"""

# Намойиш кетиб турмаса маъноси йўқ буйруқлар
_NEEDS_SHOW = {"next", "prev", "first", "last", "goto", "jump", "black", "white",
               "unblank", "notes", "mark", "to_mark"}


def format_clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return "%02d:%02d" % divmod(seconds, 60)


class CommandRouter:
    def __init__(self, backend, cooldown: float = 1.5,
                 clock: Callable[[], float] = time.monotonic, conflict_window: float = 0.8,
                 talk_minutes: float = 0, warn_minutes: float = 1,
                 auto_default_seconds: int = 15, confirm_exit: bool = True,
                 confirm_seconds: float = 6.0, gate: Optional[Callable[[float], bool]] = None,
                 session=None):
        self.backend = backend
        self.confirm_exit = confirm_exit
        self.confirm_seconds = confirm_seconds
        self.gate = gate                  # push-to-talk: gate(овоз вақти) -> эшитишга рухсатми
        self.session = session            # analytics.SessionLog ёки None
        self.bookmark: Optional[int] = None
        self._exit_deadline = 0.0
        self.cooldown = cooldown
        self.conflict_window = conflict_window
        self._last_command: Optional[Tuple[str, float]] = None
        self.clock = clock
        self.listening = True
        self.should_exit = False
        self.started = clock()
        self.talk_seconds = max(0.0, talk_minutes * 60)
        self.warn_seconds = max(0.0, warn_minutes * 60)
        self.auto_default = auto_default_seconds
        self.auto_interval: Optional[float] = None
        self._auto_due = 0.0
        self._warned = False
        self._ended = False
        self._last_seen: Dict[Tuple[str, Optional[int]], float] = {}

    # -- кириш нуқталари -------------------------------------------------------------
    def handle(self, command: Command) -> Optional[str]:
        """Буйруқни бажаради ва фойдаланувчига кўрсатиладиган хабарни қайтаради
        (None — буйруқ эътиборсиз қолдирилди)."""
        moment = command.at if command.at is not None else self.clock()

        if not command.manual:  # телефон пульти ва ҳ.к. бу текширувлардан ўтмайди
            if not self.listening and command.intent != "resume_listen":
                return None
            if command.intent in ("confirm_yes", "confirm_no") and not self._exit_pending():
                return None  # "ha"/"yo'q" оддий нутқда ҳам учрайди: тасдиқ кутилмаса эътибор берилмайди
            if self.gate is not None and not self.gate(moment):
                return None  # push-to-talk тугмаси босилмаган
            # Овоз айтилган вақт бўйича такрорни аниқлаймиз: икки мотор (Vosk ва Google)
            # бир сўзни турли пайтда қайтарса ҳам, у битта буйруқ ҳисобланади.
            key = (command.intent, command.arg)
            last = self._last_seen.get(key)
            if last is not None and abs(moment - last) < self.cooldown:
                return None
            # Бир овоздан икки хил буйруқ чиқса (турли моделлар), биринчиси қолади
            # (фақат овоз вақти маълум буйруқлар учун: мотор уни ҳар доим беради)
            if (command.at is not None and self._last_command is not None
                    and self._last_command[0] != command.intent
                    and abs(moment - self._last_command[1]) < self.conflict_window):
                return None
            self._last_seen[key] = moment
            self._last_command = (command.intent, moment)

        if command.intent in _NEEDS_SHOW and not self.backend.is_running():
            return "Намойиш кетмаяпти — «boshla» (бошла) денг"

        message = getattr(self, "_do_" + command.intent)(command)
        if self.session is not None:
            self.session.record_command(command.intent, command.arg, command.manual)
        return self._with_position(message)

    def tick(self) -> List[str]:
        """Ҳар ярим сонияда чақирилади: автоматик ўтиш ва вақт огоҳлантиришлари."""
        messages: List[str] = []
        now = self.clock()

        if self.auto_interval and now >= self._auto_due and self.backend.is_running():
            self._auto_due = now + self.auto_interval
            if self.backend.next():
                messages.append(self._with_position("Автоматик: кейинги слайд"))
            else:
                self.auto_interval = None
                messages.append("Охирги слайд — автоматик ўтиш тўхтатилди")

        if self.talk_seconds:
            remaining = self.talk_seconds - (now - self.started)
            if remaining <= 0 and not self._ended:
                self._ended = True
                self._warned = True
                messages.append("Ажратилган вақт тугади!")
            elif 0 < remaining <= self.warn_seconds and not self._warned:
                self._warned = True
                messages.append("Вақт тугашига %s қолди" % format_clock(remaining))
        return messages

    @property
    def needs_attention(self) -> bool:
        """Вақт огоҳлантиришларида овозли сигнал берилсин."""
        return self._warned or self._ended

    # -- ҳаракатлар -------------------------------------------------------------------
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

    def _do_jump(self, command):
        steps = command.arg
        position = self.backend.position()
        if position:
            current, total = position
            target = min(max(current + steps, 1), total)
            if target == current:
                return "Бу охирги слайд" if steps > 0 else "Бу биринчи слайд"
            self.backend.goto(target)
            return "%d-слайдга ўтилди" % target
        move = self.backend.next if steps > 0 else self.backend.prev  # рақамсиз режим
        for _ in range(min(abs(steps), 30)):
            move()
        return "%d слайд %s" % (abs(steps), "олдинга" if steps > 0 else "орқага")

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

    def _exit_pending(self) -> bool:
        return self.clock() < self._exit_deadline

    def _do_exit(self, command):
        if command.manual or not self.confirm_exit or self._exit_pending():
            self.should_exit = True
            return "Намойиш ёпилмоқда"
        # Чиқиш — энг "қиммат" хато: сўзни янглиш эшитиш намойишни ёпиб юбормаслиги учун тасдиқ сўралади
        self._exit_deadline = self.clock() + self.confirm_seconds
        return ("Чиқишни тасдиқланг: «ha» (ҳа) ёки яна «chiqish» денг (%d сония). "
                "Бекор қилиш: «yo'q»" % self.confirm_seconds)

    def _do_confirm_yes(self, _):
        self.should_exit = True
        return "Намойиш ёпилмоқда"

    def _do_confirm_no(self, _):
        self._exit_deadline = 0.0
        return "Чиқиш бекор қилинди"

    def _do_mark(self, _):
        position = self.backend.position()
        if not position:
            return "Белгилаб бўлмади: слайд рақами номаълум (клавиатура режими)"
        self.bookmark = position[0]
        return "%d-слайд белгиланди. Қайтиш учун «belgiga qayt» денг" % self.bookmark

    def _do_to_mark(self, _):
        if self.bookmark is None:
            return "Белги қўйилмаган — аввал «belgila» денг"
        if not self.backend.goto(self.bookmark):
            return "%d-слайд топилмади" % self.bookmark
        return "Белгига қайтилди: %d-слайд" % self.bookmark

    def _do_report(self, _):
        if self.session is None:
            return "Ҳисобот ўчирилган"
        return self.session.summary_text()

    def _do_help(self, _):
        return HELP_TEXT

    def _do_status(self, _):
        parts = []
        position = self.backend.position()
        if position:
            parts.append("слайд %d/%d" % position)
        elapsed = self.clock() - self.started
        parts.append("ўтди %s" % format_clock(elapsed))
        if self.talk_seconds:
            parts.append("қолди %s" % format_clock(self.talk_seconds - elapsed))
        if self.auto_interval:
            parts.append("автоматик: ҳар %d сония" % self.auto_interval)
        if not self.listening:
            parts.append("микрофон тўхтатилган")
        return "Ҳолат: " + ", ".join(parts)

    def _do_auto_on(self, command):
        seconds = command.arg if command.arg else self.auto_default
        seconds = min(max(int(seconds), 2), 3600)
        if not self.backend.is_running():
            return "Намойиш кетмаяпти — «boshla» (бошла) денг"
        self.auto_interval = seconds
        self._auto_due = self.clock() + seconds
        return "Автоматик ўтиш: ҳар %d сонияда. Тўхтатиш: «qo'lda» (қўлда)" % seconds

    def _do_auto_off(self, _):
        if not self.auto_interval:
            return "Автоматик ўтиш ёқилмаган"
        self.auto_interval = None
        return "Автоматик ўтиш ўчирилди"

    def _do_notes(self, _):
        text = self.backend.notes()
        if text is None:
            return "Изоҳлар бу режимда мавжуд эмас (фақат PowerPoint орқали)"
        return "Изоҳ:\n%s" % text if text else "Бу слайдда изоҳ йўқ"

    def _do_outline(self, _):
        titles = self.backend.titles()
        if titles is None:
            return "Мундарижа бу режимда мавжуд эмас (фақат PowerPoint орқали)"
        lines = ["%d. %s" % (i, t or "—") for i, t in enumerate(titles, 1)]
        return "Слайдлар:\n" + "\n".join(lines)

    # -- ёрдамчи ---------------------------------------------------------------------
    def _with_position(self, message: str) -> str:
        if "\n" in message:
            return message
        position = self.backend.position()
        return "%s  [%d/%d]" % (message, *position) if position else message
