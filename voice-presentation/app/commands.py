# -*- coding: utf-8 -*-
"""Овозли буйруқларни аниқлаш.

Соф Python (ташқи кутубхонасиз), шунинг учун Windows'сиз ҳам синаса бўлади.

Асосий ғоялар:
  * Google ўзбек тилини баъзан лотинда ("keyingisi"), баъзан кирилда
    ("кейингиси") қайтаради. Иккаласини ҳам битта лотин шаклга келтирамиз.
  * Бир неча вариант ("alternatives") текширилади, шунда тўғри сўз
    иккинчи-учинчи ўринда келса ҳам топилади.
  * Нотўғри эшитилган сўзлар учун ўхшашлик (fuzzy) ишлатилади, лекин
    "oldinga" (олдинга) ва "oldingi" (олдинги) каби яқин сўзлар
    чалкашмаслиги учун энг яхши мос келган буйруқ танланади.
  * Презентация давомида одатдаги гап буйруқ деб ўйланмаслиги учун
    фақат қисқа жумлалар қабул қилинади.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# Матнни меъёрлаштириш (кирил -> лотин, апостроф ва тиниш белгиларини олиб ташлаш)
# ---------------------------------------------------------------------------
_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "",
    "ь": "", "ы": "i", "э": "e", "ю": "yu", "я": "ya",
    "ў": "o", "қ": "q", "ғ": "g", "ҳ": "h",
}
_APOSTROPHES = "'`´ʻʼ’‘ʹ′"


def norm(text: str) -> str:
    """Матнни кичик лотин ҳарфлар ва рақамлардан иборат шаклга келтиради."""
    text = text.lower()
    text = "".join(_CYR.get(ch, ch) for ch in text)
    for ch in _APOSTROPHES:
        text = text.replace(ch, "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def tokens_of(text: str) -> List[str]:
    return norm(text).split()


def similarity(a: str, b: str, threshold: float) -> float:
    """Икки сўзнинг ўхшашлиги (0 — мос эмас). Қисқа сўзлар фақат айнан мос келади."""
    if a == b:
        return 1.0
    if min(len(a), len(b)) < 4:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio if ratio >= threshold else 0.0


# ---------------------------------------------------------------------------
# Рақамлар: ўзбек / рус / инглиз ("5", "beshinchi", "пятый", "twenty one")
# ---------------------------------------------------------------------------
_NUMBER_TABLE = [
    (1, "bir один one первый first"),
    (2, "ikki два two второй second"),
    (3, "uch три three третий third"),
    (4, "to'rt четыре four четвертый fourth"),
    (5, "besh пять five пятый fifth"),
    (6, "olti шесть six шестой sixth"),
    (7, "yetti семь seven седьмой seventh"),
    (8, "sakkiz восемь eight восьмой eighth"),
    (9, "to'qqiz девять nine девятый ninth"),
    (10, "o'n десять ten десятый tenth"),
    (11, "одиннадцать eleven"),
    (12, "двенадцать twelve"),
    (13, "тринадцать thirteen"),
    (14, "четырнадцать fourteen"),
    (15, "пятнадцать fifteen"),
    (16, "шестнадцать sixteen"),
    (17, "семнадцать seventeen"),
    (18, "восемнадцать eighteen"),
    (19, "девятнадцать nineteen"),
    (20, "yigirma двадцать twenty"),
    (30, "o'ttiz тридцать thirty"),
    (40, "qirq сорок forty"),
    (50, "ellik пятьдесят fifty"),
    (60, "oltmish шестьдесят sixty"),
    (70, "yetmish семьдесят seventy"),
    (80, "sakson восемьдесят eighty"),
    (90, "to'qson девяносто ninety"),
]
_NUMBERS: Dict[str, int] = {}
for _value, _words in _NUMBER_TABLE:
    for _word in _words.split():
        _NUMBERS[norm(_word)] = _value

# ўзбекча тартиб сонлар: birinchi, ikkinchi, uchinchi, beshinchi, yigirmanchi ...
_ORDINAL_SUFFIXES = ("inchi", "nchi", "chi", "ta")  # ... ва "beshta", "ikkita"


def word_to_number(token: str) -> Optional[int]:
    if token in _NUMBERS:
        return _NUMBERS[token]
    if token == "bitta":
        return 1
    for suffix in _ORDINAL_SUFFIXES:
        if token.endswith(suffix) and token[: -len(suffix)] in _NUMBERS:
            return _NUMBERS[token[: -len(suffix)]]
    return None


def parse_number_span(tokens: Sequence[str]) -> Optional[Tuple[int, List[int]]]:
    """Жумладаги биринчи сонни ва у эгаллаган сўзлар индексларини қайтаради."""
    current: Optional[int] = None
    used: List[int] = []
    for index, token in enumerate(tokens):
        if token.isdigit():
            if current is None:
                return int(token), [index]
            break
        value = word_to_number(token)
        if value is None:
            if current is not None:
                break
            continue
        if current is None:
            current = value
            used.append(index)
        elif current >= 10 and current % 10 == 0 and 1 <= value <= 9:
            current += value  # yigirma + besh
            used.append(index)
        else:
            break
    return (current, used) if current is not None else None


def parse_number(tokens: Sequence[str]) -> Optional[int]:
    """Жумладаги биринчи сонни топади: "5", "beshinchi", "yigirma besh" -> 25."""
    found = parse_number_span(tokens)
    return found[0] if found else None


# ---------------------------------------------------------------------------
# Буйруқлар
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Command:
    intent: str
    arg: Optional[int] = None  # goto: слайд рақами; jump: ±слайдлар сони; auto_on: сония
    # Овоз қачон айтилгани (time.monotonic). Икки мотордан келган бир хил буйруқни аниқлаш учун.
    at: Optional[float] = field(default=None, compare=False)


# Ҳар бир буйруқ учун иборалар (лотин, кирил, рус, инглиз — барчаси бирга).
INTENT_PHRASES: Dict[str, List[str]] = {
    "next": ["keyingi", "keyingisi", "davomi", "oldinga",
             "кейинги", "далее", "дальше", "следующий", "вперёд",
             "next", "forward"],
    "prev": ["oldingi", "oldingisi", "avvalgi", "avvalgisi", "orqaga", "ortga",
             "олдинги", "назад", "предыдущий", "back", "previous"],
    "first": ["boshidan", "boshiga", "qaytadan", "сначала", "restart"],
    "last": ["oxirgi", "oxirgisi", "oxiriga", "последний", "last"],
    "black": ["qora ekran", "qora", "ekranni o'chir", "qorong'i",
              "чёрный экран", "black"],
    "white": ["oq ekran", "oq", "белый экран", "white"],
    "unblank": ["ekranni och", "ekranni qaytar", "ekranni yoq", "ko'rsat", "show"],
    "pause_listen": ["tinglama", "jim", "pauza", "mikrofonni o'chir",
                     "тихо", "pause", "stop listening"],
    "resume_listen": ["tingla", "mikrofonni yoq", "слушай", "listen"],
    "start": ["boshla", "boshlash", "namoyishni boshla", "ishga tushir",
              "начать", "start", "begin"],
    "exit": ["chiqish", "chiq", "tugat", "tugatish", "yop",
             "выход", "закрыть", "exit", "quit", "close"],
    "help": ["yordam", "buyruqlar", "помощь", "help"],
    "status": ["holat", "vaqt", "qancha vaqt qoldi", "состояние", "время", "status"],
    "notes": ["izoh", "izohlar", "eslatma", "eslatmalar", "заметки", "notes"],
    "outline": ["mundarija", "ro'yxat", "оглавление", "список", "outline"],
    "auto_on": ["avtomatik", "avto", "avtomat", "автомат", "auto", "autoplay"],
    "auto_off": ["qo'lda", "qo'lda boshqar", "avtomatikni o'chir", "avtomatikni to'xtat",
                 "avtomatik to'xta", "вручную", "manual"],
}

# "2 slayd oldinga" / "3 slayd orqaga" — нисбий ўтиш
FORWARD_WORDS = ["oldinga", "forward", "вперёд", "ahead"]
BACK_WORDS = ["orqaga", "ortga", "back", "назад", "backward"]

# слайд сўзи: "5 slayd", "slayd 5", "beshinchi slayd", "5-slaydga o'tish"
SLIDE_WORDS = ["slayd", "slaydga", "slaydni", "slide", "слайд", "sahifa", "bet"]

# Буйруқ сўзидан ташқари жумлада фақат шу тўлдирувчи сўзлар бўлиши мумкин.
# Бошқа ҳар қандай сўз ("keyingi misol", "olti oy davomida") — бу оддий гап, буйруқ эмас.
FILLER_WORDS = [
    "slayd", "slaydga", "slaydni", "slaydda", "slayt", "slide", "слайд",
    "sahifa", "sahifaga", "bet", "betga",
    "o'tkaz", "o'tkazing", "o'tkazib", "o'ting", "o't", "o'tish", "yubor", "yuboring",
    "qayt", "qaytish", "qaytar", "iltimos", "endi", "hozir", "keling", "marhamat",
    "ga", "ni", "ekran", "ekranga",
    "пожалуйста", "теперь", "перейди", "перейти", "переключи", "давай", "на",
    "please", "go", "to", "the", "now", "and", "screen",
    "soniya", "soniyada", "soniyadan", "sekund", "sekundda", "second", "seconds",
    "секунд", "секунды", "секунда",
]

# Айрим буйруқлар учун жумладаги сўзлар сони чекланган (тасодифан ишга тушмаслиги учун).
MAX_WORDS_FOR_INTENT = {"exit": 2}


class CommandParser:
    def __init__(self, threshold: float = 0.85, max_words: int = 4,
                 wake_word: str = "",
                 extra_words: Optional[Dict[str, Iterable[str]]] = None):
        self.threshold = threshold
        self.max_words = max_words
        self.wake = norm(wake_word) if wake_word else ""
        self.phrases: Dict[str, List[List[str]]] = {
            intent: self._unique(tokens_of(p) for p in phrases)
            for intent, phrases in INTENT_PHRASES.items()
        }
        self.slide_words = [norm(w) for w in SLIDE_WORDS]
        self.fillers = [norm(w) for w in FILLER_WORDS]
        self.forward = [norm(w) for w in FORWARD_WORDS]
        self.back = [norm(w) for w in BACK_WORDS]
        for intent, words in (extra_words or {}).items():
            if intent not in self.phrases:
                raise ValueError(
                    "extra_words: номаълум буйруқ '%s' (мумкин: %s)"
                    % (intent, ", ".join(sorted(self.phrases))))
            self.phrases[intent] = self._unique(
                self.phrases[intent] + [tokens_of(w) for w in words if tokens_of(w)])

    @staticmethod
    def _unique(phrases) -> List[List[str]]:
        """Кирил ва лотин ёзуви бир хил бўлиб қолган такрорларни олиб ташлайди."""
        seen, result = set(), []
        for phrase in phrases:
            if phrase and tuple(phrase) not in seen:
                seen.add(tuple(phrase))
                result.append(phrase)
        return result

    # -- умумий кириш нуқтаси ------------------------------------------------
    def parse(self, texts: Iterable[str]) -> Optional[Command]:
        """Таниб олинган вариантлардан (ишончлилик тартибида) биринчи буйруқни қайтаради."""
        for text in texts:
            command = self._parse_one(text)
            if command:
                return command
        return None

    # -- ички қисм --------------------------------------------------------------
    def _parse_one(self, text: str) -> Optional[Command]:
        tokens = tokens_of(text)
        if self.wake:
            tokens = self._strip_wake_word(tokens)
        if not tokens or len(tokens) > self.max_words:
            return None

        goto = self._goto(tokens)
        if goto is not None:
            number, used = goto
            direction, direction_used = self._direction(tokens, used)
            if direction:
                used = used | direction_used
                if number < 1 or not self._only_fillers(tokens, used):
                    return None
                return Command("jump", direction * number)
            return Command("goto", number) if self._only_fillers(tokens, used) else None

        scored = sorted(
            ((*self._intent_score(tokens, intent), intent) for intent in self.phrases),
            reverse=True)
        best_score, used, best = scored[0]
        if best_score <= 0:
            return None
        # иккита ҳар хил буйруқ деярли тенг бўлса — адашмаслик учун ҳеч нима қилмаймиз
        if scored[1][0] > 0 and best_score - scored[1][0] < 0.05:
            return None
        if len(tokens) > MAX_WORDS_FOR_INTENT.get(best, self.max_words):
            return None
        arg = None
        if best == "auto_on":  # "avtomatik 20 soniya"
            found = parse_number_span(tokens)
            if found:
                arg = found[0]
                used = used | set(found[1])
        if not self._only_fillers(tokens, used):
            return None
        return Command(best, arg)

    def _direction(self, tokens: List[str], used: Set[int]) -> Tuple[int, Set[int]]:
        """"oldinga" (+1) ёки "orqaga" (-1) сўзини топади."""
        for index, tok in enumerate(tokens):
            if index in used:
                continue
            if any(similarity(tok, w, self.threshold) > 0 for w in self.forward):
                return 1, {index}
            if any(similarity(tok, w, self.threshold) > 0 for w in self.back):
                return -1, {index}
        return 0, set()

    def _only_fillers(self, tokens: List[str], used: Set[int]) -> bool:
        return all(
            any(similarity(tok, filler, 0.8) > 0 for filler in self.fillers)
            for index, tok in enumerate(tokens) if index not in used)

    def _strip_wake_word(self, tokens: List[str]) -> List[str]:
        for index, token in enumerate(tokens[:2]):
            if similarity(token, self.wake, self.threshold) > 0:
                return tokens[index + 1:]
        return []

    def _goto(self, tokens: List[str]) -> Optional[Tuple[int, Set[int]]]:
        slide_index = next(
            (i for i, tok in enumerate(tokens)
             if any(similarity(tok, word, self.threshold) > 0 for word in self.slide_words)),
            None)
        if slide_index is None:
            return None
        found = parse_number_span(tokens)
        if found is None:
            return None
        number, number_indices = found
        return number, {slide_index, *number_indices}

    def _intent_score(self, tokens: List[str], intent: str) -> Tuple[float, Set[int]]:
        best, best_used = 0.0, set()
        for phrase in self.phrases[intent]:
            size = len(phrase)
            for start in range(len(tokens) - size + 1):
                sims = [similarity(tokens[start + i], phrase[i], self.threshold)
                        for i in range(size)]
                if all(s > 0 for s in sims):
                    # узунроқ ибора ("qora ekran") қисқасидан ("qora") устун туради
                    score = sum(sims) / size * (1 + 0.1 * (size - 1))
                    if score > best:
                        best, best_used = score, set(range(start, start + size))
        return best, best_used


    # -- Vosk учун луғат ------------------------------------------------------------
    def vocabulary(self) -> Set[str]:
        """Буйруқларни айтиш учун керак бўлган барча (меъёрлаштирилган) сўзлар."""
        words: Set[str] = set()
        for phrases in self.phrases.values():
            for phrase in phrases:
                words.update(phrase)
        for group in (self.fillers, self.slide_words, self.forward, self.back, _NUMBERS):
            words.update(group)
        for base in list(_NUMBERS):  # ўзбекча тартиб сонлар: birinchi, ikkinchi ...
            words.add(base + ("nchi" if base[-1] in "aeiou" else "inchi"))
        return words

    def coverage(self, available: Set[str]) -> Dict[str, List[str]]:
        """Моделнинг луғатида тўлиқ мавжуд бўлган иборалар (ҳар бир буйруқ учун)."""
        return {
            intent: [" ".join(p) for p in phrases if all(t in available for t in p)]
            for intent, phrases in self.phrases.items()
        }
