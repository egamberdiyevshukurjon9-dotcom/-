# -*- coding: utf-8 -*-
"""Тестлар: python -m unittest discover -s tests -v   (voice-presentation/ папкасидан)

Windows'га хос қисмлар (PowerPoint COM, микрофон) бу ерда текширилмайди —
уларни ҳақиқий компьютерда `--test` режими билан синанг.
"""
import json
import os
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backends import DryRunBackend  # noqa: E402
from commands import Command, CommandParser, norm, parse_number, tokens_of  # noqa: E402
from router import CommandRouter  # noqa: E402
import presentation_voice as pv  # noqa: E402


class NormalizeTests(unittest.TestCase):
    def test_cyrillic_and_latin_become_equal(self):
        self.assertEqual(norm("кейингиси"), norm("keyingisi"))
        self.assertEqual(norm("Олдинги"), norm("oldingi"))
        self.assertEqual(norm("бошидан"), norm("boshidan"))
        self.assertEqual(norm("чиқиш"), norm("chiqish"))
        self.assertEqual(norm("қора экран"), norm("qora ekran"))
        self.assertEqual(norm("ўтказ"), norm("o'tkaz"))
        self.assertEqual(norm("o‘tkaz"), norm("oʻtkaz"))

    def test_punctuation_and_hyphen(self):
        self.assertEqual(tokens_of("5-slayd!"), ["5", "slayd"])


class NumberTests(unittest.TestCase):
    def p(self, text):
        return parse_number(tokens_of(text))

    def test_numbers(self):
        self.assertEqual(self.p("5"), 5)
        self.assertEqual(self.p("beshinchi slayd"), 5)
        self.assertEqual(self.p("birinchi"), 1)
        self.assertEqual(self.p("ikkinchi"), 2)
        self.assertEqual(self.p("uchinchi"), 3)
        self.assertEqual(self.p("to'rtinchi"), 4)
        self.assertEqual(self.p("oltinchi"), 6)
        self.assertEqual(self.p("yettinchi"), 7)
        self.assertEqual(self.p("sakkizinchi"), 8)
        self.assertEqual(self.p("o'ninchi"), 10)
        self.assertEqual(self.p("yigirmanchi"), 20)
        self.assertEqual(self.p("yigirma besh"), 25)
        self.assertEqual(self.p("o'n besh"), 15)
        self.assertEqual(self.p("пятый"), 5)
        self.assertEqual(self.p("двадцать три"), 23)
        self.assertEqual(self.p("third"), 3)
        self.assertEqual(self.p("twenty one"), 21)
        self.assertEqual(self.p("12"), 12)

    def test_no_number(self):
        self.assertIsNone(self.p("keyingi slayd"))


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = CommandParser()

    def intent(self, *texts):
        command = self.parser.parse(texts)
        return command.intent if command else None

    def test_next(self):
        for text in ["keyingisi", "keyingi", "кейингиси", "Кейинги", "keyingi slayd",
                     "keyingisiga o'tkaz", "davomi", "давоми", "next", "далее",
                     "oldinga", "keying", "keyingisi."]:
            self.assertEqual(self.intent(text), "next", text)

    def test_prev(self):
        for text in ["oldingisi", "oldingi", "олдингиси", "orqaga", "орқага",
                     "back", "назад", "oldingi slayd", "avvalgisi"]:
            self.assertEqual(self.intent(text), "prev", text)

    def test_forward_and_previous_are_not_confused(self):
        self.assertEqual(self.intent("oldinga"), "next")
        self.assertEqual(self.intent("oldingi"), "prev")
        # аниқ эшитилмаган, иккаласига ҳам тенг яқин сўз — ҳеч нима қилинмайди
        self.assertIsNone(self.intent("oldingo"))

    def test_other_commands(self):
        cases = {
            "boshidan": "first", "бошидан": "first", "qaytadan": "first",
            "oxirgi slayd": "last", "охирги": "last",
            "qora ekran": "black", "қора экран": "black", "oq ekran": "white",
            "ekranni och": "unblank", "ko'rsat": "unblank",
            "tinglama": "pause_listen", "tingla": "resume_listen",
            "boshla": "start", "chiqish": "exit", "чиқиш": "exit", "tugat": "exit",
            "yordam": "help",
        }
        for text, expected in cases.items():
            self.assertEqual(self.intent(text), expected, text)

    def test_goto(self):
        for text, number in [("5 slayd", 5), ("slayd 7", 7), ("beshinchi slayd", 5),
                             ("5-slaydga o'tish", 5), ("10 слайд", 10),
                             ("birinchi slayd", 1), ("go to slide 3", 3)]:
            self.assertEqual(self.parser.parse([text]), Command("goto", number), text)

    def test_ordinary_speech_is_ignored(self):
        for text in [
            "bugun biz ekologiya haqida gaplashamiz",
            "keyin ko'rsataman",
            "bu juda muhim masala",
            "men chiqish haqida gapirmoqchiman",       # exit: 2 сўздан узун
            "keyingi slaydda biz natijalarni ko'ramiz",  # узун жумла
            "5 ta misol keltiraman",                      # рақам бор, "slayd" йўқ
            "keyingi misol", "keyingi slaydda ko'ramiz", "5 slaydda ko'ramiz",
            "olti oy davomida", "davom etamiz", "oxirgi yillarda",
            "qora rangli", "oq rang bilan", "следующий вопрос",
            "",
        ]:
            self.assertIsNone(self.intent(text), text)

    def test_polite_filler_words_are_allowed(self):
        for text, expected in [("keyingi slaydga o'tkaz", "next"),
                               ("iltimos keyingisi", "next"),
                               ("next slide please", "next"),
                               ("следующий слайд", "next"),
                               ("oldingi slaydga qayt", "prev"),
                               ("keyingi slayd", "next")]:
            self.assertEqual(self.intent(text), expected, text)
        self.assertEqual(self.parser.parse(["iltimos 5 slaydga o'tkaz"]), Command("goto", 5))

    def test_alternatives_are_all_checked(self):
        self.assertEqual(self.intent("keyin gina", "keyingisi"), "next")

    def test_wake_word(self):
        parser = CommandParser(wake_word="slayd")
        self.assertIsNone(parser.parse(["keyingisi"]))
        self.assertEqual(parser.parse(["slayd keyingisi"]), Command("next"))

    def test_extra_words(self):
        parser = CommandParser(extra_words={"next": ["davay"]})
        self.assertEqual(parser.parse(["davay"]), Command("next"))
        with self.assertRaises(ValueError):
            CommandParser(extra_words={"nope": ["x"]})


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.backend = DryRunBackend(total=5)
        self.clock = Clock()
        self.router = CommandRouter(self.backend, cooldown=1.0, clock=self.clock)

    def test_next_prev_and_bounds(self):
        self.assertIn("[2/5]", self.router.handle(Command("next")))
        self.clock.now += 2
        self.assertIn("[1/5]", self.router.handle(Command("prev")))
        self.clock.now += 2
        self.assertIn("биринчи", self.router.handle(Command("prev")))
        self.assertEqual(self.backend.current, 1)
        self.router.handle(Command("last"))
        self.clock.now += 2
        self.assertIn("охирги", self.router.handle(Command("next")))
        self.assertEqual(self.backend.current, 5)

    def test_cooldown_ignores_duplicates_only_briefly(self):
        self.assertIsNotNone(self.router.handle(Command("next")))
        self.clock.now += 0.3
        self.assertIsNone(self.router.handle(Command("next")))
        self.assertEqual(self.backend.current, 2)
        self.clock.now += 1.0
        self.assertIsNotNone(self.router.handle(Command("next")))
        self.assertEqual(self.backend.current, 3)

    def test_goto(self):
        self.assertIn("[4/5]", self.router.handle(Command("goto", 4)))
        self.assertIn("йўқ", self.router.handle(Command("goto", 9)))
        self.assertEqual(self.backend.current, 4)

    def test_blank_and_navigation_restores(self):
        self.router.handle(Command("black"))
        self.assertEqual(self.backend.blank, "black")
        self.clock.now += 2
        self.router.handle(Command("next"))
        self.assertIsNone(self.backend.blank)
        self.assertEqual(self.backend.current, 2)

    def test_pause_and_resume_listening(self):
        self.router.handle(Command("pause_listen"))
        self.assertIsNone(self.router.handle(Command("next")))
        self.assertEqual(self.backend.current, 1)
        self.assertIsNotNone(self.router.handle(Command("resume_listen")))
        self.clock.now += 2
        self.router.handle(Command("next"))
        self.assertEqual(self.backend.current, 2)

    def test_exit_sets_flag(self):
        self.router.handle(Command("exit"))
        self.assertTrue(self.router.should_exit)

    def test_show_not_running(self):
        self.backend.exit_show()  # масалан фойдаланувчи Esc босди
        self.assertIn("boshla", self.router.handle(Command("next")))
        self.assertEqual(self.backend.current, 1)
        self.router.handle(Command("start"))
        self.assertTrue(self.backend.is_running())
        self.clock.now += 2
        self.router.handle(Command("next"))
        self.assertEqual(self.backend.current, 2)

    def test_help(self):
        self.assertIn("кейингиси", self.router.handle(Command("help")))


class DiscoveryTests(unittest.TestCase):
    def test_find_presentation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(pv.find_presentation(root))
            (root / "~$presentation.pptx").write_bytes(b"lock")
            self.assertIsNone(pv.find_presentation(root))
            (root / "other.pptx").write_bytes(b"x")
            (root / "Presentation.PPTX").write_bytes(b"x")
            self.assertEqual(pv.find_presentation(root).name, "Presentation.PPTX")
            (root / "Presentation.PPTX").unlink()
            self.assertEqual(pv.find_presentation(root).name, "other.pptx")

    def test_find_in_subfolder_and_ignores_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("x")
            (root / "Taqdimot").mkdir()
            (root / "Taqdimot" / "dars.ppsx").write_bytes(b"x")
            self.assertEqual(pv.find_presentation(root).name, "dars.ppsx")

    def test_load_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(pv.load_config(root)["max_words"], 4)
            (root / "config.json").write_text(
                json.dumps({"max_words": 6, "languages": "ru-RU"}), encoding="utf-8-sig")
            config = pv.load_config(root)
            self.assertEqual(config["max_words"], 6)
            self.assertEqual(config["languages"], ["ru-RU"])
            (root / "config.json").write_text("{buzilgan", encoding="utf-8")
            self.assertEqual(pv.load_config(root)["max_words"], 4)

    def test_list_drives_off_windows(self):
        if sys.platform != "win32":
            self.assertEqual(pv.list_drives(), [])


class FakeRecognizerListenerTests(unittest.TestCase):
    """VoiceListener мантиғи сохта таниш билан (интернет ва микрофонсиз)."""

    def setUp(self):
        try:
            import speech_recognition as sr
        except ImportError:
            self.skipTest("SpeechRecognition ўрнатилмаган")
        self.sr = sr
        self.results = queue.Queue()
        config = dict(pv.DEFAULT_CONFIG)
        self.listener = pv.VoiceListener(config, CommandParser(), self.results)
        self.listener.sr = sr

    def audio(self, seconds):
        rate, width = 16000, 2
        return self.sr.AudioData(b"\x00" * int(rate * width * seconds), rate, width)

    class Fake:
        def __init__(self, sr, outcome):
            self.sr, self.outcome, self.calls = sr, outcome, []

        def recognize_google(self, audio, language, show_all):
            self.calls.append(language)
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome

    def test_command_is_queued(self):
        self.listener.recognizer = self.Fake(self.sr, {"alternative": [
            {"transcript": "bugun"}, {"transcript": "keyingisi"}], "final": True})
        self.listener._recognize(self.audio(1))
        self.assertEqual(self.results.get_nowait(), Command("next"))

    def test_unknown_value_is_silent(self):
        self.listener.recognizer = self.Fake(self.sr, self.sr.UnknownValueError())
        self.listener._recognize(self.audio(1))
        self.assertTrue(self.results.empty())

    def test_request_error_does_not_crash(self):
        self.listener.recognizer = self.Fake(self.sr, self.sr.RequestError("offline"))
        self.listener._recognize(self.audio(1))
        self.assertTrue(self.results.empty())

    def test_second_language_tried_when_no_command(self):
        self.listener.config = dict(self.listener.config, languages=["uz-UZ", "ru-RU"])
        fake = self.Fake(self.sr, {"alternative": [{"transcript": "салом"}]})
        self.listener.recognizer = fake
        self.listener._recognize(self.audio(1))
        self.assertEqual(fake.calls, ["uz-UZ", "ru-RU"])

    def test_long_audio_is_not_sent_to_the_internet(self):
        self.listener._on_audio(None, self.audio(3.5))  # узун, лекин кесилмаган
        self.assertTrue(self.listener._audio.empty())
        self.listener._on_audio(None, self.audio(1))
        self.assertFalse(self.listener._audio.empty())

    def test_tail_of_cut_off_speech_is_dropped(self):
        self.listener._on_audio(None, self.audio(4.0))  # phrase_time_limit да кесилди
        self.listener._on_audio(None, self.audio(1.0))  # нутқнинг "думи"
        self.assertTrue(self.listener._audio.empty())
        self.listener._on_audio(None, self.audio(1.0))  # янги, мустақил жумла
        self.assertEqual(self.listener._audio.qsize(), 1)

    def test_audio_queue_keeps_newest(self):
        for _ in range(10):
            self.listener._on_audio(None, self.audio(1))
        self.assertLessEqual(self.listener._audio.qsize(), 4)


class SessionTests(unittest.TestCase):
    """run_session занжири: тинглагич -> буйруқ -> слайд -> чиқиш."""

    def run_with(self, commands):
        backend = DryRunBackend(total=10)
        closed = []
        backend.close = lambda: closed.append(True)

        class FakeListener:
            stopped = False

            def __init__(self, config, parser, results):
                self.results = results

            def prepare(self):
                pass

            def begin(self):
                for command in commands:
                    self.results.put(command)

            def stop(self):
                FakeListener.stopped = True

        args = SimpleNamespace(test=False)
        with mock.patch.object(pv, "VoiceListener", FakeListener), \
                mock.patch.object(pv, "open_backend", lambda *a: backend):
            pv.run_session(None, args, dict(pv.DEFAULT_CONFIG), CommandParser())
        return backend, closed, FakeListener.stopped

    def test_commands_then_exit(self):
        backend, closed, stopped = self.run_with(
            [Command("next"), Command("goto", 4), Command("exit"), Command("next")])
        self.assertEqual(backend.current, 4)  # "exit"дан кейинги буйруқ бажарилмайди
        self.assertTrue(closed)
        self.assertTrue(stopped)


if __name__ == "__main__":
    unittest.main()
