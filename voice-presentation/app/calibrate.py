# -*- coding: utf-8 -*-
"""Калибровка: тизим сизнинг овозингизда қанчалик аниқ ишлашини ўлчайди ва шахсийлаштиради.

Икки режим:
  --calibrate            микрофон орқали: дастур буйруқларни навбат билан айттиради (ҳар бирини N марта),
                         ҳар бир моторнинг аниқлигини (%) ва нотўғри эшитилган сўзларни кўрсатади;
  --calibrate-wav ПАПКА  тайёр овоз файллари билан (next_1.wav, goto5_2.wav ...) — микрофонсиз.

Нотўғри, лекин такрорланадиган эшитишлар ("keyingisi" ўрнига ҳар сафар "kegingi") тасдиқингиз
билан `learned.json` га шахсий алиас сифатида ёзилади ва кейинги ишга туширишда ишлатилади.
Аниқлик ўлчови ва шахсийлаштириш — таниш сифатини "ишонинг" дейиш эмас, ўлчаш учун восита.
"""
import datetime
import json
import logging
import queue
import re
import time
import wave
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from audio import RATE, Segmenter
from commands import Command, CommandParser, norm, tokens_of
from engines import GoogleEngine, VoskEngine, read_wav_pcm

log = logging.getLogger("voicepres")

# (буйруқ, рақам, нима дейиш керак)
CORE_STEPS: List[Tuple[str, Optional[int], str]] = [
    ("next", None, "keyingisi"), ("prev", None, "oldingisi"), ("first", None, "boshidan"),
    ("last", None, "oxirgi slayd"), ("goto", 5, "beshinchi slayd"), ("black", None, "qora ekran"),
    ("white", None, "oq ekran"), ("exit", None, "chiqish"), ("pause_listen", None, "jim"),
    ("resume_listen", None, "tingla"),
]
WAV_NAME = re.compile(r"^(?P<intent>[a-z_]+?)(?P<arg>\d+)?_\d+\.wav$")
RecognizeFn = Callable[[bytes], Tuple[List[str], Optional[Command]]]


@dataclass
class Sample:
    intent: str
    arg: Optional[int]
    pcm: bytes
    label: str = ""


@dataclass
class EngineResult:
    name: str
    hits: Counter = field(default_factory=Counter)
    total: Counter = field(default_factory=Counter)
    misses: List[Tuple[str, str, List[str], Optional[str]]] = field(default_factory=list)
    # (кутилган буйруқ, белги, эшитилган матнлар, нотўғри аниқланган буйруқ)

    def accuracy(self) -> float:
        total = sum(self.total.values())
        return sum(self.hits.values()) / total if total else 0.0


# ---------------------------------------------------------------------------
# Тан олувчилар (мотор ёки Google орқали бир овозни матн ва буйруққа айлантириш)
# ---------------------------------------------------------------------------
def vosk_recognizer(engine: VoskEngine) -> RecognizeFn:
    def run(pcm: bytes):
        outcomes = engine.process_pcm(pcm)
        for _language, texts, command in outcomes:  # модель устуворлиги: биринчи топилган буйруқ
            if command:
                return texts, command
        return (outcomes[0][1] if outcomes else []), None
    return run


def google_recognizer(engine: GoogleEngine, parser: CommandParser) -> RecognizeFn:
    def run(pcm: bytes):
        heard = engine.transcribe(pcm) or []
        for _language, texts in heard:
            command = parser.parse(texts)
            if command:
                return texts, command
        return (heard[0][1] if heard else []), None
    return run


def matches(command: Optional[Command], intent: str, arg: Optional[int]) -> bool:
    return bool(command) and command.intent == intent and (arg is None or command.arg == arg)


def evaluate(samples: List[Sample], recognizers: Dict[str, RecognizeFn]) -> Dict[str, EngineResult]:
    results = {name: EngineResult(name) for name in recognizers}
    for sample in samples:
        key = sample.intent if sample.arg is None else "%s%d" % (sample.intent, sample.arg)
        for name, recognize in recognizers.items():
            texts, command = recognize(sample.pcm)
            result = results[name]
            result.total[key] += 1
            if matches(command, sample.intent, sample.arg):
                result.hits[key] += 1
            else:
                result.misses.append((key, sample.label, texts, command.intent if command else None))
    return results


# ---------------------------------------------------------------------------
# Шахсий алиаслар
# ---------------------------------------------------------------------------
def suggest_aliases(result: EngineResult, parser: CommandParser, min_count: int = 2) -> Dict[str, List[str]]:
    """Бир буйруқ учун камида `min_count` марта такрорланган, буйруқ деб танилмаган эшитишлар."""
    known = set(parser.vocabulary())
    counts: Dict[str, Counter] = defaultdict(Counter)
    for key, _label, texts, wrong in result.misses:
        if wrong is not None or not texts:
            continue  # бошқа буйруққа ўхшаб кетган сўзни алиас қилиш хавфли
        tokens = tokens_of(texts[0])
        if 0 < len(tokens) <= 2 and all(len(t) >= 4 and t not in known for t in tokens):
            counts[re.sub(r"\d+$", "", key)][" ".join(tokens)] += 1
    taken = {" ".join(p) for phrases in parser.phrases.values() for p in phrases}
    return {intent: sorted(w for w, n in counter.items() if n >= min_count and w not in taken)
            for intent, counter in counts.items()
            if any(n >= min_count for n in counter.values())}


def load_learned(path: Path, valid_intents) -> Dict[str, List[str]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        aliases = data.get("aliases", {})
    except (OSError, ValueError, AttributeError):
        return {}
    return {intent: [w for w in words if isinstance(w, str) and w.strip()]
            for intent, words in aliases.items() if intent in valid_intents and isinstance(words, list)}


def save_learned(path: Path, new_aliases: Dict[str, List[str]], valid_intents, summary: Optional[dict] = None
                 ) -> Dict[str, List[str]]:
    merged = load_learned(path, valid_intents)
    for intent, words in new_aliases.items():
        merged[intent] = sorted(set(merged.get(intent, [])) | set(words))
    history = []
    try:
        history = json.loads(Path(path).read_text(encoding="utf-8-sig")).get("history", [])
    except (OSError, ValueError, AttributeError):
        pass
    if summary:
        history.append(dict(summary, date=datetime.datetime.now().isoformat(timespec="seconds")))
    Path(path).write_text(json.dumps({"aliases": merged, "history": history[-20:]},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


# ---------------------------------------------------------------------------
# Ҳисобот
# ---------------------------------------------------------------------------
def format_report(results: Dict[str, EngineResult]) -> str:
    lines = []
    for name, result in results.items():
        total = sum(result.total.values())
        lines.append("== %s: %d/%d тўғри (%.0f%%)" % (name, sum(result.hits.values()), total, 100 * result.accuracy()))
        for key in sorted(result.total):
            hit, count = result.hits[key], result.total[key]
            lines.append("   %-14s %d/%d %s" % (key, hit, count, "" if hit == count else "  <-- қийин"))
        for key, label, texts, wrong in result.misses[:12]:
            lines.append("   ✗ %s: эшитилди «%s»%s" % (
                key, texts[0] if texts else "—", " (бошқа буйруқ деб ўйлади: %s)" % wrong if wrong else ""))
    if len(results) > 1:
        best = max(results.values(), key=lambda r: r.accuracy())
        lines.append("Тавсия: энг аниқ натижа — %s (%.0f%%). Иккаласи ҳам паст бўлса, \"engine\": \"both\" синаб кўринг."
                     % (best.name, 100 * best.accuracy()))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Файллардан
# ---------------------------------------------------------------------------
def samples_from_wav_dir(folder: Path) -> List[Sample]:
    samples = []
    for path in sorted(Path(folder).glob("*.wav")):
        match = WAV_NAME.match(path.name.lower())
        if not match:
            log.warning("Файл номи тушунарсиз (кутилган: next_1.wav, goto5_2.wav): %s", path.name)
            continue
        arg = int(match.group("arg")) if match.group("arg") else None
        samples.append(Sample(match.group("intent"), arg, read_wav_pcm(path), path.name))
    return samples


# ---------------------------------------------------------------------------
# Микрофондан
# ---------------------------------------------------------------------------
def capture_phrase(blocks: "queue.Queue", timeout: float = 7.0, min_energy: float = 300.0) -> Optional[bytes]:
    """Микрофон бўлакларидан битта жумлани ажратиб олади (None — гапирилмади)."""
    segmenter = Segmenter(pause=0.7, max_phrase=5.0, min_energy=min_energy)
    deadline = time.monotonic() + timeout + 1.0  # +1 с: шовқин даражасини ўлчаш
    while time.monotonic() < deadline:
        try:
            stamp, block = blocks.get(timeout=0.5)
        except queue.Empty:
            continue
        segment = segmenter.feed(block, stamp)
        if segment:
            return segment.audio
    return None


def record_samples(blocks: "queue.Queue", repeats: int, say: Callable[[str], None],
                   ask: Callable[[str], str], min_energy: float = 300.0,
                   steps=CORE_STEPS, capture=capture_phrase) -> List[Sample]:
    say("Ҳар бир буйруқни %d марта айтасиз. Enter босинг, 1 сония кутинг ва равон айтинг.\n" % repeats)
    samples: List[Sample] = []
    for intent, arg, phrase in steps:
        for attempt in range(1, repeats + 1):
            ask("[%s %d/%d] Айтинг: «%s»  (Enter) " % (intent, attempt, repeats, phrase))
            while not blocks.empty():  # Enter босилгунча йиғилган эски овозни ташлаймиз
                blocks.get_nowait()
            pcm = capture(blocks, min_energy=min_energy)
            if pcm is None:
                say("   овоз эшитилмади — ўтказиб юборилди")
                continue
            samples.append(Sample(intent, arg, pcm, phrase))
    return samples


def run_calibration(samples: List[Sample], recognizers: Dict[str, RecognizeFn], parser: CommandParser,
                    learned_path: Path, say: Callable[[str], None] = print,
                    ask: Callable[[str], str] = input, assume_yes: bool = False) -> Dict[str, EngineResult]:
    if not samples:
        say("Ҳеч қандай овоз ёзилмади.")
        return {}
    results = evaluate(samples, recognizers)
    say("\n" + format_report(results))
    valid = set(parser.phrases)
    for name, result in results.items():
        suggestions = suggest_aliases(result, parser)
        approved: Dict[str, List[str]] = {}
        for intent, words in suggestions.items():
            for word in words:
                answer = "ha" if assume_yes else ask("«%s» (%s) сўзини «%s» буйруғи деб қабул қилайми? [ha/yo'q] " % (
                    word, name, intent)).strip().lower()
                if answer in ("ha", "h", "xa", "y", "yes", "да"):
                    approved.setdefault(intent, []).append(word)
        summary = {"engine": name, "accuracy": round(result.accuracy(), 3), "samples": sum(result.total.values())}
        saved = save_learned(learned_path, approved, valid, summary)
        if approved:
            say("Сақланди: %s → %s" % (learned_path, json.dumps(approved, ensure_ascii=False)))
    return results
