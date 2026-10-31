# -*- coding: utf-8 -*-
import csv
import json
import tempfile
import unittest
from pathlib import Path

import _setup  # noqa: F401
from analytics import SessionLog, clock_text
from commands import Command


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.log = SessionLog(clock=self.clock, wall=lambda: 1_700_000_000.0, title="Дарс")

    def view(self, slide, total, seconds):
        self.log.observe_position((slide, total))
        self.clock.now += seconds

    def test_time_per_slide_and_visits(self):
        self.view(1, 5, 30)
        self.view(2, 5, 90)
        self.view(1, 5, 10)         # орқага қайтди
        self.log.observe_position((1, 5))
        data = self.log.to_dict()
        by_slide = {row["slide"]: row for row in data["slides"]}
        self.assertEqual(by_slide[1]["seconds"], 40.0)
        self.assertEqual(by_slide[1]["visits"], 2)
        self.assertEqual(by_slide[2]["seconds"], 90.0)
        self.assertEqual(data["total_slides"], 5)
        self.assertEqual(data["slides_visited"], 2)

    def test_paused_show_does_not_count(self):
        self.view(3, 5, 20)
        self.log.observe_position(None)    # намойиш тўхтади
        self.clock.now += 500
        self.log.observe_position(None)
        self.assertEqual(self.log.to_dict()["slides"][0]["seconds"], 20.0)

    def test_commands_and_recognition_rate(self):
        self.log.record_command("next")
        self.log.record_command("next", manual=True)
        self.log.record_command("prev")
        self.log.record_heard(["keyingi"], Command("next"))
        self.log.record_heard(["bugun ob-havo yaxshi"], None)
        self.log.record_heard(["oldingi"], Command("prev"))
        data = self.log.to_dict()
        self.assertEqual(data["commands"], {"next": 2, "prev": 1})
        self.assertEqual((data["utterances_heard"], data["utterances_accepted"]), (3, 2))
        self.assertEqual(data["remote_commands"], 1)
        text = self.log.summary_text()
        self.assertIn("3 та жумла эшитилди, 2 таси буйруқ (67%)", text)
        self.assertIn("next ×2", text)
        self.assertIn("Телефон пультидан: 1", text)

    def test_summary_lists_longest_slides(self):
        self.view(1, 9, 10)
        self.view(2, 9, 200)
        self.view(3, 9, 50)
        self.log.observe_position((3, 9))
        text = self.log.summary_text()
        self.assertIn("2-слайд 03:20", text)
        self.assertIn("Кўрилган слайдлар: 3 (жами 9 та)", text)

    def test_save_writes_json_csv_html(self):
        self.view(1, 2, 12)
        self.view(2, 2, 3)
        self.log.observe_position((2, 2))
        self.log.record_command("next")
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.log.save(Path(tmp) / "reports")
            self.assertEqual([p.suffix for p in paths], [".json", ".csv", ".html"])
            data = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(data["title"], "Дарс")
            self.assertEqual(data["started"][:4], "2023")
            with open(paths[1], encoding="utf-8-sig") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[0], ["slide", "seconds", "visits"])
            self.assertEqual(rows[1][:2], ["1", "12.0"])
            html = paths[2].read_text(encoding="utf-8")
            self.assertIn("<table>", html)
            self.assertIn("next ×1", html)

    def test_html_escapes_command_names(self):
        self.log.record_command("<script>")
        html = SessionLog.to_html(self.log.to_dict())
        self.assertNotIn("<script>", html)

    def test_unwritable_directory_does_not_raise(self):
        with tempfile.NamedTemporaryFile() as handle:
            self.assertEqual(self.log.save(Path(handle.name) / "reports"), [])   # файл ичида папка ясаб бўлмайди

    def test_clock_text(self):
        self.assertEqual(clock_text(65), "01:05")
        self.assertEqual(clock_text(3725), "1:02:05")
        self.assertEqual(clock_text(-4), "00:00")


if __name__ == "__main__":
    unittest.main()
