# -*- coding: utf-8 -*-
import unittest

import _setup  # noqa: F401
from backends import DryRunBackend
from commands import Command
from router import CommandRouter


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

    def test_same_utterance_from_two_engines_runs_once(self):
        # Vosk 10.0 да, Google 3 сониядан кейин қайтарди, лекин овоз бир пайтда айтилган
        self.assertIsNotNone(self.router.handle(Command("next", None, 10.0)))
        self.clock.now += 3
        self.assertIsNone(self.router.handle(Command("next", None, 10.4)))
        self.assertEqual(self.backend.current, 2)
        # ҳақиқатан янги айтилган буйруқ бажарилади
        self.assertIsNotNone(self.router.handle(Command("next", None, 13.0)))
        self.assertEqual(self.backend.current, 3)

    def test_conflicting_commands_from_one_utterance_keep_the_first(self):
        # битта овоз: бир модель "keyingi", иккинчиси "orqaga" деб эшитди
        self.assertIsNotNone(self.router.handle(Command("next", None, 20.0)))
        self.assertIsNone(self.router.handle(Command("prev", None, 20.1)))
        self.assertEqual(self.backend.current, 2)
        # 1 сониядан кейинги ҳақиқий буйруқ бажарилади
        self.assertIsNotNone(self.router.handle(Command("prev", None, 21.5)))
        self.assertEqual(self.backend.current, 1)

    def test_goto(self):
        self.assertIn("[4/5]", self.router.handle(Command("goto", 4)))
        self.assertIn("йўқ", self.router.handle(Command("goto", 9)))
        self.assertEqual(self.backend.current, 4)

    def test_jump(self):
        self.router.handle(Command("jump", 3))
        self.assertEqual(self.backend.current, 4)
        self.clock.now += 2
        self.router.handle(Command("jump", 10))  # охиригача кесилади
        self.assertEqual(self.backend.current, 5)
        self.clock.now += 2
        self.assertIn("охирги", self.router.handle(Command("jump", 1)))
        self.clock.now += 2
        self.router.handle(Command("jump", -2))
        self.assertEqual(self.backend.current, 3)

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
        self.backend.exit_show()
        self.assertIn("boshla", self.router.handle(Command("next")))
        self.assertEqual(self.backend.current, 1)
        self.router.handle(Command("start"))
        self.assertTrue(self.backend.is_running())
        self.clock.now += 2
        self.router.handle(Command("next"))
        self.assertEqual(self.backend.current, 2)

    def test_help_status_notes_outline(self):
        self.assertIn("кейингиси", self.router.handle(Command("help")))
        self.clock.now += 65
        status = self.router.handle(Command("status"))
        self.assertIn("слайд 1/5", status)
        self.assertIn("01:05", status)
        self.assertIn("Синов изоҳи", self.router.handle(Command("notes")))
        outline = self.router.handle(Command("outline"))
        self.assertIn("5. Синов слайди 5", outline)

    def test_notes_unavailable_in_other_backends(self):
        self.backend.notes = lambda: None
        self.backend.titles = lambda: None
        self.assertIn("мавжуд эмас", self.router.handle(Command("notes")))
        self.assertIn("мавжуд эмас", self.router.handle(Command("outline")))


class AutoAndTimerTests(unittest.TestCase):
    def setUp(self):
        self.backend = DryRunBackend(total=3)
        self.clock = Clock()

    def router(self, **kwargs):
        return CommandRouter(self.backend, cooldown=1.0, clock=self.clock, **kwargs)

    def test_auto_advance_until_last_slide(self):
        router = self.router()
        self.assertIn("ҳар 10 сонияда", router.handle(Command("auto_on", 10)))
        self.clock.now += 9
        self.assertEqual(router.tick(), [])
        self.clock.now += 2
        self.assertIn("[2/3]", router.tick()[0])
        self.clock.now += 10
        router.tick()
        self.assertEqual(self.backend.current, 3)
        self.clock.now += 10
        self.assertIn("тўхтатилди", router.tick()[0])
        self.assertIsNone(router.auto_interval)

    def test_auto_default_and_off(self):
        router = self.router(auto_default_seconds=7)
        self.assertIn("ҳар 7 сонияда", router.handle(Command("auto_on")))
        self.clock.now += 2
        self.assertIn("ўчирилди", router.handle(Command("auto_off")))
        self.clock.now += 20
        self.assertEqual(router.tick(), [])
        self.assertEqual(self.backend.current, 1)

    def test_auto_seconds_are_limited(self):
        router = self.router()
        router.handle(Command("auto_on", 0))
        self.assertEqual(router.auto_interval, router.auto_default)  # 0 -> стандарт қиймат
        router.handle(Command("auto_on", 99999))
        self.assertEqual(router.auto_interval, 3600)

    def test_talk_timer_warns_once_and_ends(self):
        router = self.router(talk_minutes=10, warn_minutes=1)
        self.clock.now += 8 * 60
        self.assertEqual(router.tick(), [])
        self.clock.now += 61
        messages = router.tick()
        self.assertEqual(len(messages), 1)
        self.assertIn("00:59", messages[0])  # 10 дақиқа - 8:01 = 59 сония
        self.assertEqual(router.tick(), [])
        self.clock.now += 120
        self.assertIn("тугади", router.tick()[0])
        self.assertEqual(router.tick(), [])
        self.assertTrue(router.needs_attention)

    def test_no_timer_when_disabled(self):
        router = self.router(talk_minutes=0)
        self.clock.now += 99999
        self.assertEqual(router.tick(), [])
        self.assertFalse(router.needs_attention)


if __name__ == "__main__":
    unittest.main()
