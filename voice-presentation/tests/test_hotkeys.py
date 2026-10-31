# -*- coding: utf-8 -*-
import time
import unittest

import _setup  # noqa: F401
from hotkeys import PushToTalk, key_code


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class PushToTalkTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.down = False
        self.ptt = PushToTalk("f9", key_state=lambda vk: self.down, clock=self.clock)

    def step(self, seconds, down):
        self.down = down
        self.ptt.poll_once()
        self.clock.now += seconds

    def test_key_codes(self):
        self.assertEqual(key_code("F9"), 0x78)
        self.assertEqual(key_code("ctrl"), 0x11)
        self.assertEqual(key_code("f1"), 0x70)
        self.assertEqual(key_code("f12"), 0x7B)
        with self.assertRaises(ValueError):
            key_code("q1")

    def test_nothing_is_allowed_without_the_key(self):
        self.step(5, False)
        self.assertFalse(self.ptt.allows(self.clock.now))
        self.assertFalse(self.ptt.allows(0))

    def test_allowed_while_held_and_shortly_after_release(self):
        self.step(0.1, True)             # 100.0 да босилди
        self.step(2.0, True)             # 100.1 .. 102.1 ушлаб турилди
        self.assertTrue(self.ptt.allows(101.0))
        self.step(0.1, False)            # 102.1 да қўйиб юборилди
        self.assertTrue(self.ptt.allows(102.5))      # Vosk жумлани 0,6 с кейин тугатади
        self.assertTrue(self.ptt.allows(103.5))      # grace=1.5 ичида
        self.assertFalse(self.ptt.allows(104.5))     # grace тугади
        self.assertFalse(self.ptt.allows(99.0))      # босилишдан олдинги овоз

    def test_each_press_is_separate(self):
        self.step(1, True)
        self.step(1, False)              # 101..102 интервал
        self.step(10, False)
        self.step(1, True)               # 112 да яна босилди
        self.assertFalse(self.ptt.allows(108.0))     # оралиқдаги нутқ қабул қилинмайди
        self.assertTrue(self.ptt.allows(112.5))

    def test_background_thread_samples_the_key(self):
        state = {"down": False}
        ptt = PushToTalk("space", key_state=lambda vk: state["down"], poll=0.005)
        ptt.start()
        state["down"] = True
        time.sleep(0.1)
        self.assertTrue(ptt.allows(time.monotonic()))
        ptt.stop()

    @unittest.skipIf(__import__("sys").platform == "win32", "Linux учун")
    def test_windows_only_without_injected_state(self):
        with self.assertRaises(OSError):
            PushToTalk("f9")


if __name__ == "__main__":
    unittest.main()
