# -*- coding: utf-8 -*-
import ctypes
import json
import os
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _setup  # noqa: F401
import presentation_voice as pv
import winkeys
from backends import DryRunBackend
from commands import Command, CommandParser
from errors import AudioError


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

    def test_subfolder_and_ignored_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("x")
            (root / "VoiceApp").mkdir()
            (root / "VoiceApp" / "ichki.pptx").write_bytes(b"x")  # дастурнинг ўз папкаси
            self.assertIsNone(pv.find_presentation(root))
            (root / "Taqdimot").mkdir()
            (root / "Taqdimot" / "dars.ppsx").write_bytes(b"x")
            self.assertEqual(pv.find_presentation(root).name, "dars.ppsx")

    def test_list_drives_off_windows(self):
        if sys.platform != "win32":
            self.assertEqual(pv.list_drives(), [])


class ConfigTests(unittest.TestCase):
    def test_load_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(pv.load_config(root)["max_words"], 4)
            (root / "config.json").write_text(
                json.dumps({"max_words": 6, "languages": "ru-RU", "_izoh": "x", "mic_index": 2}),
                encoding="utf-8-sig")
            config = pv.load_config(root)
            self.assertEqual(config["max_words"], 6)
            self.assertEqual(config["languages"], ["ru-RU"])
            self.assertEqual(config["mic"], 2)          # эски калит номи
            self.assertNotIn("_izoh", config)
            (root / "config.json").write_text("{buzilgan", encoding="utf-8")
            self.assertEqual(pv.load_config(root)["max_words"], 4)

    def test_shipped_config_is_valid(self):
        shipped = Path(__file__).resolve().parent.parent / "flash" / "VoiceApp" / "config.json"
        data = json.loads(shipped.read_text(encoding="utf-8"))
        unknown = [k for k in data if not k.startswith("_") and k not in pv.DEFAULT_CONFIG]
        self.assertEqual(unknown, [])
        pv.make_parser(dict(pv.DEFAULT_CONFIG, **{k: v for k, v in data.items() if not k.startswith("_")}))

    def test_root_dir_from_environment(self):
        with mock.patch.dict(os.environ, {"VOICE_ROOT": "E:\\"}):
            self.assertEqual(pv.root_dir(), Path("E:\\"))
        with mock.patch.dict(os.environ, {"VOICE_ROOT": '"/media/flash/"'}):
            self.assertEqual(pv.root_dir(), Path("/media/flash"))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(pv.root_dir().exists())


class WinkeysTests(unittest.TestCase):
    def test_structure_matches_windows_x64(self):
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(winkeys.INPUT), 40)

    def test_arrow_keys_use_extended_flag(self):
        down, up = winkeys._key_events(winkeys.VK["right"], True)
        self.assertEqual(down.ki.dwFlags, winkeys.KEYEVENTF_EXTENDEDKEY)
        self.assertEqual(up.ki.dwFlags, winkeys.KEYEVENTF_EXTENDEDKEY | winkeys.KEYEVENTF_KEYUP)
        self.assertEqual(down.type, winkeys.INPUT_KEYBOARD)

    @unittest.skipIf(sys.platform == "win32", "Linux учун")
    def test_press_fails_clearly_off_windows(self):
        with self.assertRaises(OSError):
            winkeys.press("right")

    def test_digits_only(self):
        with self.assertRaises(ValueError):
            winkeys.type_digits("1a")


class FakeAudio:
    fail = None
    stopped = False

    def __init__(self, *args, **kwargs):
        self.queues = []

    def start(self):
        if FakeAudio.fail:
            raise FakeAudio.fail
        return "Sinov mikrofoni"

    def subscribe(self, maxsize=100):
        q = queue.Queue()
        self.queues.append(q)
        return q

    def stop(self):
        FakeAudio.stopped = True


class FakeEngine:
    name = "Fake"
    started = False

    def __init__(self, commands, results):
        self.commands, self.results = commands, results

    def start(self, audio_queue):
        FakeEngine.started = True
        for command in self.commands:
            self.results.put(command)

    def stop(self):
        pass


class SessionTests(unittest.TestCase):
    def setUp(self):
        FakeAudio.fail, FakeAudio.stopped, FakeEngine.started = None, False, False
        self.backend = DryRunBackend(total=10)
        self.closed = []
        self.backend.close = lambda: self.closed.append(True)

    def run_session(self, commands, engine_error=None, backend_error=None):
        def fake_load(config, parser, emit, root):
            if engine_error:
                raise engine_error
            return [FakeEngine(commands, emit.__self__ if hasattr(emit, "__self__") else emit)]

        def fake_open(*args):
            if backend_error:
                raise backend_error
            return self.backend

        # emit = results.put (bound method) — навбатнинг ўзини оламиз
        def load_engines(config, parser, emit, root):
            results = emit.__self__
            if engine_error:
                raise engine_error
            return [FakeEngine(commands, results)]

        with mock.patch.object(pv, "AudioInput", FakeAudio), \
                mock.patch.object(pv, "load_engines", load_engines), \
                mock.patch.object(pv, "open_backend", fake_open):
            return pv.run_session(None, SimpleNamespace(test=False), dict(pv.DEFAULT_CONFIG),
                                  CommandParser())

    def test_commands_then_exit(self):
        code = self.run_session([Command("next"), Command("goto", 4), Command("exit"), Command("next")])
        self.assertEqual(code, 0)
        self.assertEqual(self.backend.current, 4)   # "exit"дан кейинги буйруқ бажарилмайди
        self.assertTrue(self.closed)
        self.assertTrue(FakeAudio.stopped)
        self.assertTrue(FakeEngine.started)

    def test_no_microphone_keeps_presentation_open(self):
        FakeAudio.fail = AudioError("Микрофон топилмади")
        code = self.run_session([Command("next")])
        self.assertEqual(code, 2)
        self.assertEqual(self.closed, [])           # презентация ёпилмайди
        self.assertEqual(self.backend.current, 1)
        self.assertTrue(FakeAudio.stopped)

    def test_engine_failure_keeps_presentation_open(self):
        code = self.run_session([], engine_error=pv.EngineError("модель йўқ"))
        self.assertEqual(code, 2)
        self.assertEqual(self.closed, [])

    def test_backend_failure_stops_audio_and_raises(self):
        with self.assertRaises(pv.SetupError):
            self.run_session([], backend_error=pv.SetupError("PowerPoint йўқ"))
        self.assertTrue(FakeAudio.stopped)


class MainTests(unittest.TestCase):
    def test_missing_file(self):
        self.assertEqual(pv.main(["--file", "/yo'q/fayl.pptx"]), 1)

    def test_word_check_without_models_is_a_clear_error(self):
        self.assertEqual(pv.main(["--word-check", "keyingi,oldingi"]), 1)

    def test_selftest_runs_and_reports(self):
        # Linux'да sounddevice йўқ — [X] чиқиши керак, лекин дастур ўзи қулаб тушмаслиги шарт
        code = pv.selftest(dict(pv.DEFAULT_CONFIG), CommandParser())
        self.assertIn(code, (0, 1))


if __name__ == "__main__":
    unittest.main()
