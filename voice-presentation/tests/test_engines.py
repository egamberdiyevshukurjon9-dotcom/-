# -*- coding: utf-8 -*-
import json
import queue
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _setup  # noqa: F401
import engines
from audio import RATE, Segment
from commands import Command, CommandParser
from engines import (GoogleEngine, VoskEngine, ascii_safe_path, build_engines, build_grammar,
                     discover_models, guess_language, load_engines, read_vocabulary,
                     read_wav_pcm, texts_from_result)
from errors import EngineError

CONFIG = {
    "engine": "auto", "vosk_models": ["uz", "ru", "en"], "vosk_grammar": True,
    "vosk_alternatives": 3, "languages": ["uz-UZ"], "pause_threshold": 0.6,
    "phrase_time_limit": 4.0, "max_command_seconds": 3.0, "min_energy": 300, "verbose": False,
}


def make_model(root: Path, name: str, words=()):
    model = root / name
    (model / "am").mkdir(parents=True, exist_ok=True)
    (model / "am" / "final.mdl").write_bytes(b"x")
    (model / "graph").mkdir(exist_ok=True)
    lines = ["<eps> 0", "!SIL 1", "#0 2"] + ["%s %d" % (w, i + 3) for i, w in enumerate(words)]
    (model / "graph" / "words.txt").write_text("\n".join(lines), encoding="utf-8")
    return model


class FakeRecognizer:
    instances = []

    def __init__(self, model, rate, grammar=None):
        self.grammar, self.calls, self.alternatives = grammar, 0, None
        self.reply = {"alternatives": [{"text": "keyingisi", "confidence": 1.0}]}
        FakeRecognizer.instances.append(self)

    def SetMaxAlternatives(self, n):
        self.alternatives = n

    def AcceptWaveform(self, data):
        self.calls += 1
        return self.calls % 3 == 0

    def Result(self):
        return json.dumps(self.reply)

    def FinalResult(self):
        return json.dumps({"text": ""})


FAKE_VOSK = SimpleNamespace(
    SetLogLevel=lambda level: None, Model=lambda path: SimpleNamespace(path=path),
    KaldiRecognizer=FakeRecognizer)


class ModelDiscoveryTests(unittest.TestCase):
    def test_guess_language(self):
        self.assertEqual(guess_language("vosk-model-small-uz-0.22"), "uz")
        self.assertEqual(guess_language("ru"), "ru")
        self.assertEqual(guess_language("vosk-model-small-en-us-0.15"), "en")

    def test_discover(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_model(root, "uz")
            make_model(root, "vosk-model-small-ru-0.22")
            (root / "bo'sh").mkdir()
            nested = root / "en"
            (nested / "inner" / "am").mkdir(parents=True)
            (nested / "inner" / "am" / "final.mdl").write_bytes(b"x")
            found = discover_models(root)
            self.assertEqual(sorted(found), ["en", "ru", "uz"])
            self.assertEqual(found["en"].name, "inner")
            self.assertEqual(sorted(discover_models(root, ["uz"])), ["uz"])
            self.assertEqual(discover_models(root / "yo'q"), {})

    def test_vocabulary_and_grammar(self):
        parser = CommandParser()
        with tempfile.TemporaryDirectory() as tmp:
            model = make_model(Path(tmp), "uz", ["keyingisi", "кейингиси", "slayd", "o'tkaz",
                                                 "bugun", "besh", "x" * 40])
            vocabulary = read_vocabulary(model)
            self.assertNotIn("eps", vocabulary)
            self.assertEqual(sorted(vocabulary["keyingisi"]), ["keyingisi", "кейингиси"])
            words, missing = build_grammar(parser, vocabulary)
            self.assertIn("keyingisi", words)
            self.assertIn("кейингиси", words)
            self.assertIn("o'tkaz", words)
            self.assertNotIn("bugun", words)   # буйруқ сўзи эмас
            self.assertIn("oldingi", missing)

    def test_no_vocabulary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "uz"
            (model / "am").mkdir(parents=True)
            self.assertIsNone(read_vocabulary(model))

    def test_texts_from_result(self):
        self.assertEqual(texts_from_result('{"text": "keyingi"}'), ["keyingi"])
        self.assertEqual(texts_from_result('{"text": ""}'), [])
        self.assertEqual(texts_from_result('{"alternatives": [{"text": "a [unk]"}, {"text": ""}]}'),
                         ["a unk"])

    def test_ascii_path_is_unchanged_off_windows(self):
        path = Path("/tmp/модель")  # Windows'да мавжуд бўлмаган йўл ҳам ўзгармай қайтади
        self.assertEqual(ascii_safe_path(path), str(path))

    def test_wav_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            good, bad = Path(tmp) / "good.wav", Path(tmp) / "bad.wav"
            for path, rate in ((good, RATE), (bad, 8000)):
                with wave.open(str(path), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(rate)
                    handle.writeframes(b"\x01\x00" * 100)
            self.assertEqual(len(read_wav_pcm(good)), 200)
            with self.assertRaises(EngineError):
                read_wav_pcm(bad)


class VoskEngineTests(unittest.TestCase):
    def setUp(self):
        FakeRecognizer.instances.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.models = {"uz": make_model(root, "uz", ["keyingisi", "slayd", "oldingi"])}
        self.emitted = []

    def engine(self, **config):
        return VoskEngine(dict(CONFIG, **config), CommandParser(), self.emitted.append,
                          self.models, vosk_module=FAKE_VOSK)

    def test_load_builds_grammar_from_model_vocabulary(self):
        engine = self.engine()
        engine.load()
        recognizer = FakeRecognizer.instances[0]
        grammar = json.loads(recognizer.grammar)
        self.assertIn("keyingisi", grammar)
        self.assertIn("[unk]", grammar)
        self.assertNotIn("bugun", grammar)
        self.assertEqual(recognizer.alternatives, 3)
        self.assertIn("oldingi", engine.report["uz"]["coverage"]["prev"])
        self.assertIn("next", engine.report["uz"]["coverage"])

    def test_free_mode_has_no_grammar(self):
        self.engine(vosk_grammar=False).load()
        self.assertIsNone(FakeRecognizer.instances[0].grammar)

    def test_feed_emits_command_with_timestamp(self):
        engine = self.engine()
        engine.load()
        for stamp in (1.0, 1.1, 1.2):
            engine.feed(b"\x00" * 3200, stamp)
        self.assertEqual(self.emitted, [Command("next")])
        self.assertEqual(self.emitted[0].at, 1.2)

    def test_non_command_speech_is_ignored(self):
        engine = self.engine()
        engine.load()
        FakeRecognizer.instances[0].reply = {"alternatives": [{"text": "unk unk keyingisi"}]}
        for stamp in (1.0, 1.1, 1.2):
            engine.feed(b"\x00" * 3200, stamp)
        self.assertEqual(self.emitted, [])

    def test_process_pcm_collects_without_emitting(self):
        engine = self.engine()
        engine.load()
        results = engine.process_pcm(b"\x00" * 3200 * 6)
        self.assertTrue(results)
        language, texts, command = results[0]
        self.assertEqual((language, texts, command), ("uz", ["keyingisi"], Command("next")))
        self.assertEqual(self.emitted, [])

    def test_broken_model_gives_clear_error(self):
        def boom(path):
            raise RuntimeError("Failed to create a model")
        engine = VoskEngine(CONFIG, CommandParser(), self.emitted.append, self.models,
                            vosk_module=SimpleNamespace(SetLogLevel=lambda l: None, Model=boom))
        with self.assertRaises(EngineError) as ctx:
            engine.load()
        self.assertIn("Failed to create a model", str(ctx.exception))

    def test_thread_loop_processes_queue(self):
        engine = self.engine()
        engine.load()
        q = queue.Queue()
        engine.start(q)
        for i in range(3):
            q.put((float(i), b"\x00" * 3200))
        deadline = __import__("time").time() + 3
        while not self.emitted and __import__("time").time() < deadline:
            __import__("time").sleep(0.02)
        engine.stop()
        self.assertEqual(self.emitted[0].intent, "next")


class FakeSR:
    class UnknownValueError(Exception):
        pass

    class RequestError(Exception):
        pass

    @staticmethod
    def AudioData(frames, rate, width):
        return SimpleNamespace(frame_data=frames, sample_rate=rate, sample_width=width)

    def __init__(self, outcome):
        self.outcome, self.calls = outcome, []
        self.Recognizer = lambda: self

    def recognize_google(self, audio, language, show_all):
        self.calls.append(language)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class GoogleEngineTests(unittest.TestCase):
    def make(self, outcome, **config):
        self.emitted = []
        self.sr = FakeSR(outcome)
        engine = GoogleEngine(dict(CONFIG, **config), CommandParser(), self.emitted.append, self.sr)
        engine.load()
        return engine

    def segment(self, seconds, cut=False, stamp=5.0):
        return Segment(b"\x00" * int(RATE * 2 * seconds), seconds, cut, stamp)

    def test_command_is_emitted_with_stamp(self):
        engine = self.make({"alternative": [{"transcript": "bugun"}, {"transcript": "keyingisi"}]})
        engine.recognize(self.segment(1))
        self.assertEqual(self.emitted, [Command("next")])
        self.assertEqual(self.emitted[0].at, 5.0)

    def test_unknown_value_and_request_error_are_silent(self):
        self.make(FakeSR.UnknownValueError()).recognize(self.segment(1))
        self.assertEqual(self.emitted, [])
        engine = self.make(FakeSR.RequestError("offline"))
        engine.recognize(self.segment(1))
        self.assertEqual(self.emitted, [])

    def test_second_language_tried_when_no_command(self):
        engine = self.make({"alternative": [{"transcript": "салом"}]}, languages=["uz-UZ", "ru-RU"])
        engine.recognize(self.segment(1))
        self.assertEqual(self.sr.calls, ["uz-UZ", "ru-RU"])

    def test_long_speech_and_its_tail_are_dropped(self):
        engine = self.make({"alternative": []})
        engine.on_segment(self.segment(3.5))
        self.assertTrue(engine._segments.empty())
        engine.on_segment(self.segment(4.0, cut=True))   # кесилган нутқ
        engine.on_segment(self.segment(1.0))             # унинг "думи"
        self.assertTrue(engine._segments.empty())
        engine.on_segment(self.segment(1.0))             # янги жумла
        self.assertEqual(engine._segments.qsize(), 1)

    def test_queue_keeps_newest(self):
        engine = self.make({"alternative": []})
        for _ in range(10):
            engine.on_segment(self.segment(1))
        self.assertLessEqual(engine._segments.qsize(), 4)


class EngineSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def build(self, mode, with_models=True, vosk_installed=True):
        if with_models:
            make_model(self.root, "uz")
        spec = object() if vosk_installed else None
        with mock.patch.object(engines.importlib.util, "find_spec", return_value=spec):
            return build_engines(dict(CONFIG, engine=mode), CommandParser(), lambda c: None, self.root)

    def names(self, engine_list):
        return [type(e).__name__ for e in engine_list]

    def test_modes(self):
        self.assertEqual(self.names(self.build("auto")), ["VoskEngine"])
        self.assertEqual(self.names(self.build("vosk")), ["VoskEngine"])
        self.assertEqual(self.names(self.build("google")), ["GoogleEngine"])
        self.assertEqual(self.names(self.build("both")), ["VoskEngine", "GoogleEngine"])

    def test_auto_without_models_uses_google(self):
        self.assertEqual(self.names(self.build("auto", with_models=False)), ["GoogleEngine"])
        self.assertEqual(self.names(self.build("auto", vosk_installed=False)), ["GoogleEngine"])

    def test_vosk_without_models_is_an_error(self):
        with self.assertRaises(EngineError) as ctx:
            self.build("vosk", with_models=False)
        self.assertIn("models", str(ctx.exception))

    def test_unknown_mode(self):
        with self.assertRaises(EngineError):
            self.build("magic")

    def test_auto_falls_back_to_google_when_model_fails(self):
        make_model(self.root, "uz")
        config = dict(CONFIG, engine="auto")
        with mock.patch.object(engines.importlib.util, "find_spec", return_value=object()), \
                mock.patch.object(VoskEngine, "load", side_effect=EngineError("buzuq")), \
                mock.patch.object(GoogleEngine, "load", return_value=None):
            loaded = load_engines(config, CommandParser(), lambda c: None, self.root)
        self.assertEqual(self.names(loaded), ["GoogleEngine"])

    def test_explicit_vosk_does_not_fall_back(self):
        make_model(self.root, "uz")
        with mock.patch.object(engines.importlib.util, "find_spec", return_value=object()), \
                mock.patch.object(VoskEngine, "load", side_effect=EngineError("buzuq")):
            with self.assertRaises(EngineError):
                load_engines(dict(CONFIG, engine="vosk"), CommandParser(), lambda c: None, self.root)


if __name__ == "__main__":
    unittest.main()
