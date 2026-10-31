# -*- coding: utf-8 -*-
import array
import json
import math
import queue
import tempfile
import unittest
import wave
from pathlib import Path

import _setup  # noqa: F401
from audio import BLOCK_FRAMES, RATE
from calibrate import (EngineResult, Sample, capture_phrase, evaluate, format_report, load_learned,
                       matches, record_samples, run_calibration, samples_from_wav_dir, save_learned,
                       suggest_aliases)
from commands import INTENT_PHRASES, Command, CommandParser


def scripted(answers):
    """Ҳар чақирувда навбатдаги (матнлар, буйруқ) ни қайтарадиган сохта тан олувчи."""
    items = list(answers)
    return lambda pcm: items.pop(0)


def samples(*pairs):
    return [Sample(intent, arg, b"\x00\x00", intent) for intent, arg in pairs]


class EvaluateTests(unittest.TestCase):
    def test_hits_and_misses(self):
        recognizer = scripted([
            (["keyingisi"], Command("next")),
            (["kegingi"], None),                       # эшитди, лекин буйруқ эмас
            (["oldingisi"], Command("prev")),
            (["besh slayd"], Command("goto", 5)),
            (["to'rt slayd"], Command("goto", 4)),     # нотўғри рақам — хато
            (["qora"], Command("white")),              # бошқа буйруқ — хато
        ])
        results = evaluate(samples(("next", None), ("next", None), ("prev", None),
                                   ("goto", 5), ("goto", 5), ("black", None)), {"vosk": recognizer})
        result = results["vosk"]
        self.assertEqual(sum(result.total.values()), 6)
        self.assertEqual(sum(result.hits.values()), 3)
        self.assertAlmostEqual(result.accuracy(), 0.5)
        self.assertEqual(result.hits["goto5"], 1)
        wrong = {key: w for key, _l, _t, w in result.misses}
        self.assertEqual(wrong["black"], "white")
        self.assertIsNone(wrong["next"])
        text = format_report(results)
        self.assertIn("3/6 тўғри (50%)", text)
        self.assertIn("next           1/2   <-- қийин", text)

    def test_matches(self):
        self.assertTrue(matches(Command("goto", 5), "goto", 5))
        self.assertTrue(matches(Command("next"), "next", None))
        self.assertFalse(matches(Command("goto", 4), "goto", 5))
        self.assertFalse(matches(None, "next", None))

    def test_report_recommends_the_better_engine(self):
        good = scripted([(["keyingi"], Command("next"))])
        bad = scripted([([], None)])
        text = format_report(evaluate(samples(("next", None)), {"Vosk": bad, "Google": good}))
        self.assertIn("энг аниқ натижа — Google", text)


class AliasTests(unittest.TestCase):
    def setUp(self):
        self.parser = CommandParser()

    def result(self, *misses):
        result = EngineResult("vosk")
        result.misses = [(key, key, texts, wrong) for key, texts, wrong in misses]
        return result

    def test_repeated_unrecognised_words_become_aliases(self):
        result = self.result(("next", ["kegingi"], None), ("next", ["kegingi"], None),
                             ("next", ["boshqa"], None), ("prev", ["olding"], None))
        suggestions = suggest_aliases(result, self.parser)
        self.assertEqual(suggestions, {"next": ["kegingi"]})      # фақат 2 марта такрорланганлар

    def test_dangerous_candidates_are_skipped(self):
        result = self.result(
            ("next", ["ha"], None), ("next", ["ha"], None),                       # қисқа сўз
            ("next", ["oldingi"], "prev"), ("next", ["oldingi"], "prev"),         # бошқа буйруққа ўхшаган
            ("next", ["keyingi"], None), ("next", ["keyingi"], None),             # аллақачон буйруқ сўзи
            ("next", ["bir ikki uch"], None), ("next", ["bir ikki uch"], None),   # жуда узун
            ("next", [], None), ("next", [], None))
        self.assertEqual(suggest_aliases(result, self.parser), {})

    def test_goto_misses_map_to_goto_intent_without_number(self):
        result = self.result(("goto5", ["beshinchi slaydi"], None), ("goto5", ["beshinchi slaydi"], None))
        self.assertEqual(suggest_aliases(result, self.parser), {})   # 2 сўз эмас: "beshinchi", "slaydi" - бири "known"

    def test_learned_file_roundtrip_and_merge(self):
        valid = set(INTENT_PHRASES)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "learned.json"
            self.assertEqual(load_learned(path, valid), {})
            save_learned(path, {"next": ["kegingi"]}, valid, {"engine": "vosk", "accuracy": 0.8})
            merged = save_learned(path, {"next": ["keygin"], "prev": ["oldigi"]}, valid)
            self.assertEqual(merged, {"next": ["kegingi", "keygin"], "prev": ["oldigi"]})
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["history"][0]["accuracy"], 0.8)
            # Бузилган ёки нотўғри файл ва номаълум буйруқлар парсерни йиқитмайди
            path.write_text(json.dumps({"aliases": {"next": ["x1"], "yoq": ["y"], "prev": "xato"}}), encoding="utf-8")
            self.assertEqual(load_learned(path, valid), {"next": ["x1"]})
            path.write_text("{buzuq", encoding="utf-8")
            self.assertEqual(load_learned(path, valid), {})

    def test_learned_aliases_work_in_the_parser(self):
        parser = CommandParser(extra_words={"next": ["kegingi"]})
        self.assertEqual(parser.parse(["kegingi"]), Command("next"))
        self.assertIn("kegingi", parser.candidate_words())       # Vosk грамматикасига тушиши учун


class WizardTests(unittest.TestCase):
    def test_run_calibration_saves_only_approved_aliases(self):
        asked, said = [], []
        recognizer = scripted([(["kegingi"], None), (["kegingi"], None), (["keyingisi"], Command("next"))])
        parser = CommandParser()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "learned.json"
            results = run_calibration(samples(("next", None), ("next", None), ("next", None)),
                                      {"Vosk": recognizer}, parser, path, say=said.append,
                                      ask=lambda q: asked.append(q) or "ha")
            self.assertEqual(load_learned(path, set(INTENT_PHRASES)), {"next": ["kegingi"]})
        self.assertEqual(len(asked), 1)
        self.assertIn("«kegingi»", asked[0])
        self.assertAlmostEqual(results["Vosk"].accuracy(), 1 / 3)

    def test_declined_aliases_are_not_saved(self):
        recognizer = scripted([(["kegingi"], None), (["kegingi"], None)])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "learned.json"
            run_calibration(samples(("next", None), ("next", None)), {"Vosk": recognizer},
                            CommandParser(), path, say=lambda t: None, ask=lambda q: "yo'q")
            self.assertEqual(load_learned(path, set(INTENT_PHRASES)), {})

    def test_no_samples(self):
        said = []
        self.assertEqual(run_calibration([], {}, CommandParser(), Path("x"), say=said.append), {})
        self.assertIn("ёзилмади", said[0])


def tone(seconds, amplitude=9000):
    count = int(RATE * seconds)
    return array.array("h", (int(amplitude * math.sin(i * 0.3)) for i in range(count))).tobytes()


def feed(q, audio):
    step = BLOCK_FRAMES * 2
    for offset in range(0, len(audio), step):
        q.put((float(offset), audio[offset:offset + step].ljust(step, b"\x00")))


class RecordingTests(unittest.TestCase):
    def test_capture_phrase_extracts_one_utterance(self):
        q = queue.Queue()
        feed(q, b"\x00" * (RATE * 2) + tone(1.0) + b"\x00" * (RATE * 2))
        pcm = capture_phrase(q, timeout=2)
        self.assertIsNotNone(pcm)
        self.assertGreater(len(pcm) / (RATE * 2), 0.9)

    def test_silence_returns_none(self):
        q = queue.Queue()
        feed(q, b"\x00" * (RATE * 3))
        self.assertIsNone(capture_phrase(q, timeout=0.3))

    def test_record_samples_collects_and_skips_silence(self):
        blocks = queue.Queue()
        results = [b"pcm-1", None, b"pcm-3"]
        prompts = []
        collected = record_samples(
            blocks, repeats=3, say=lambda t: None, ask=lambda q: prompts.append(q) or "",
            steps=[("next", None, "keyingisi")], capture=lambda b, **kw: results.pop(0))
        self.assertEqual([s.pcm for s in collected], [b"pcm-1", b"pcm-3"])
        self.assertEqual(len(prompts), 3)
        self.assertIn("keyingisi", prompts[0])


class WavFolderTests(unittest.TestCase):
    def test_names_are_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("next_1.wav", "goto5_2.wav", "pause_listen_1.wav", "yaroqsiz.wav"):
                with wave.open(str(Path(tmp) / name), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(RATE)
                    handle.writeframes(b"\x01\x00" * 50)
            found = samples_from_wav_dir(Path(tmp))
            self.assertEqual([(s.intent, s.arg) for s in found],
                             [("goto", 5), ("next", None), ("pause_listen", None)])


if __name__ == "__main__":
    unittest.main()
