# -*- coding: utf-8 -*-
"""Сессия ҳисоботи: ҳар бир слайдда қанча турилди, қайси буйруқлар қанча ишлатилди,
овоздан неча жумла эшитилиб, нечтаси буйруқ бўлди.

Маълумот йиғиш (қайд қилиш) овозга ва PowerPoint'га боғлиқ эмас, шунинг учун тўлиқ синалади.
Ҳисобот консолга чиқади ва `reports/` папкасига JSON, CSV, HTML кўринишида ёзилади.
"""
import csv
import datetime
import html
import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger("voicepres")


def clock_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return "%d:%02d:%02d" % (hours, minutes, secs) if hours else "%02d:%02d" % (minutes, secs)


class SessionLog:
    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time, title: str = ""):
        self.clock, self.wall = clock, wall
        self.title = title
        self.started = clock()
        self.started_wall = wall()
        self.slide_seconds: Dict[int, float] = {}
        self.slide_visits: Dict[int, int] = {}
        self.total_slides = 0
        self.commands: Counter = Counter()
        self.remote_commands = 0
        self.utterances = 0       # эшитилган жумлалар (буйруқ ёки оддий нутқ)
        self.accepted = 0         # улардан буйруққа айланганлари
        self._current: Optional[int] = None
        self._since = self.started

    # -- қайд қилиш ---------------------------------------------------------------
    def observe_position(self, position: Optional[Tuple[int, int]]) -> None:
        """Ҳар тикда чақирилади: ҳозирги слайдда ўтказилган вақтни ҳисоблайди."""
        now = self.clock()
        if self._current is not None:
            self.slide_seconds[self._current] = self.slide_seconds.get(self._current, 0.0) + (now - self._since)
        self._since = now
        if position is None:       # намойиш кетмаяпти
            self._current = None
            return
        index, total = position
        self.total_slides = max(self.total_slides, total)
        if index != self._current:
            self.slide_visits[index] = self.slide_visits.get(index, 0) + 1
        self._current = index

    def record_command(self, intent: str, arg: Optional[int] = None, manual: bool = False) -> None:
        self.commands[intent] += 1
        if manual:
            self.remote_commands += 1

    def record_heard(self, texts: List[str], command) -> None:
        """Таниш мотори жумла эшитганда чақиради (command — аниқланган буйруқ ёки None)."""
        self.utterances += 1
        if command is not None:
            self.accepted += 1

    # -- натижа --------------------------------------------------------------------
    def elapsed(self) -> float:
        return self.clock() - self.started

    def _snapshot_seconds(self) -> Dict[int, float]:
        seconds = dict(self.slide_seconds)
        if self._current is not None:  # ҳозирги слайднинг тугалланмаган вақти
            seconds[self._current] = seconds.get(self._current, 0.0) + (self.clock() - self._since)
        return seconds

    def to_dict(self) -> dict:
        seconds = self._snapshot_seconds()
        return {
            "title": self.title,
            "started": datetime.datetime.fromtimestamp(self.started_wall).isoformat(timespec="seconds"),
            "duration_seconds": round(self.elapsed(), 1),
            "total_slides": self.total_slides,
            "slides_visited": len(seconds),
            "slides": [{"slide": i, "seconds": round(seconds[i], 1), "visits": self.slide_visits.get(i, 0)}
                       for i in sorted(seconds)],
            "commands": dict(self.commands),
            "remote_commands": self.remote_commands,
            "utterances_heard": self.utterances,
            "utterances_accepted": self.accepted,
        }

    def summary_text(self, top: int = 3) -> str:
        data = self.to_dict()
        lines = ["Ҳисобот: давомийлиги %s" % clock_text(data["duration_seconds"])]
        if data["slides"]:
            total = " (жами %d та)" % data["total_slides"] if data["total_slides"] else ""
            lines.append("  Кўрилган слайдлар: %d%s" % (data["slides_visited"], total))
            average = sum(s["seconds"] for s in data["slides"]) / len(data["slides"])
            longest = sorted(data["slides"], key=lambda s: s["seconds"], reverse=True)[:top]
            lines.append("  Ўртача: %s; энг узоқ: %s" % (
                clock_text(average),
                ", ".join("%d-слайд %s" % (s["slide"], clock_text(s["seconds"])) for s in longest)))
        if self.commands:
            lines.append("  Буйруқлар: " + ", ".join(
                "%s ×%d" % (name, count) for name, count in self.commands.most_common()))
        if self.utterances:
            lines.append("  Овоздан %d та жумла эшитилди, %d таси буйруқ (%d%%)" % (
                self.utterances, self.accepted, round(100 * self.accepted / self.utterances)))
        if self.remote_commands:
            lines.append("  Телефон пультидан: %d та буйруқ" % self.remote_commands)
        return "\n".join(lines)

    # -- сақлаш --------------------------------------------------------------------
    def save(self, directory) -> List[Path]:
        """JSON, CSV ва HTML ҳисобот ёзади; папка ёзилмаса — бўш рўйхат (намойишга халақит бермайди)."""
        data = self.to_dict()
        try:
            folder = Path(directory)
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.fromtimestamp(self.started_wall).strftime("%Y%m%d-%H%M%S")
            base = folder / ("session-" + stamp)
            base.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            with open(base.with_suffix(".csv"), "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(["slide", "seconds", "visits"])
                for row in data["slides"]:
                    writer.writerow([row["slide"], row["seconds"], row["visits"]])
            base.with_suffix(".html").write_text(self.to_html(data), encoding="utf-8")
            return [base.with_suffix(ext) for ext in (".json", ".csv", ".html")]
        except OSError as error:
            log.warning("Ҳисобот сақланмади: %s", error)
            return []

    @staticmethod
    def to_html(data: dict) -> str:
        longest = max([s["seconds"] for s in data["slides"]] + [1.0])
        rows = "".join(
            "<tr><td>%d</td><td>%s</td><td>%d</td><td><div class=bar style=\"width:%d%%\"></div></td></tr>"
            % (s["slide"], clock_text(s["seconds"]), s["visits"], round(100 * s["seconds"] / longest))
            for s in data["slides"])
        commands = ", ".join("%s ×%d" % (html.escape(k), v) for k, v in sorted(data["commands"].items())) or "—"
        accepted = data["utterances_accepted"]
        heard = data["utterances_heard"]
        return """<!doctype html><html lang="uz"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Намойиш ҳисоботи</title>
<style>body{font:16px system-ui,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem}
table{border-collapse:collapse;width:100%%}td,th{padding:.35rem .6rem;border-bottom:1px solid #8884;text-align:left}
.bar{background:#2b7de9;height:.8rem;border-radius:3px}</style>
<h1>Намойиш ҳисоботи</h1>
<p>Бошланиши: %s<br>Давомийлиги: <b>%s</b>; кўрилган слайдлар: <b>%d</b> (жами %d)</p>
<table><tr><th>Слайд</th><th>Вақт</th><th>Кириш</th><th></th></tr>%s</table>
<p>Буйруқлар: %s<br>Овоз: %d жумла эшитилди, %d таси буйруқ<br>Телефон пультидан: %d</p></html>
""" % (html.escape(data["started"]), clock_text(data["duration_seconds"]), data["slides_visited"],
       data["total_slides"], rows, commands, heard, accepted, data["remote_commands"])
