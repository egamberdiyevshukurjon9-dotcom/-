# -*- coding: utf-8 -*-
"""Флешкага тушадиган .bat файллар учун оддий "линтер".

Windows'сиз уларни ишга тушириб бўлмайди, лекин энг кенг тарқалган хатоларни тутиш мумкин:
  * ( ... ) блоки ичидаги echo да ёпилмаган қавс — блокни вақтидан олдин ёпади
    (CI да UNINSTALL_AUTOSTART.bat шундай йиқилган: ". was unexpected at this time");
  * мавжуд бўлмаган :label'га goto;
  * ASCII бўлмаган ҳарф ёки LF-only қатор охири.
"""
import re
import unittest
from pathlib import Path

FLASH = Path(__file__).resolve().parent.parent / "flash"
BATS = sorted(FLASH.glob("*.bat"))


def lint(text: str):
    problems = []
    lines = text.split("\r\n")
    labels = {l.strip()[1:].lower() for l in lines if l.strip().startswith(":") and not l.strip().startswith("::")}
    depth = 0
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if line.startswith(")"):
            depth -= 1
        if depth > 0 and re.match(r"(?i)echo\b", line):
            body = line.replace("^(", "").replace("^)", "")
            if "(" in body or ")" in body:
                problems.append("%d: echo блок ичида қавс билан: %s" % (number, line))
        if line.endswith("("):
            depth += 1
        for target in re.findall(r"(?i)goto\s+:(\w+)", line):
            if target.lower() != "eof" and target.lower() not in labels:
                problems.append("%d: goto :%s — label йўқ" % (number, target))
    if depth != 0:
        problems.append("қавслар баланси бузилган (%d)" % depth)
    return problems


class BatchFileTests(unittest.TestCase):
    def test_there_are_scripts(self):
        names = {p.name for p in BATS}
        self.assertTrue({"START.bat", "TEST.bat", "TEST_VOICE.bat", "INSTALL_AUTOSTART.bat",
                         "UNINSTALL_AUTOSTART.bat", "PULT.bat", "KALIBROVKA.bat", "HISOBOT.bat"} <= names, names)

    def test_ascii_and_crlf(self):
        for path in BATS:
            data = path.read_bytes()
            self.assertTrue(data.isascii(), "%s: ASCII эмас" % path.name)
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""), "%s: LF-only қатор охири" % path.name)

    def test_no_known_syntax_traps(self):
        for path in BATS:
            self.assertEqual(lint(path.read_bytes().decode("ascii")), [], path.name)

    def test_linter_catches_the_ci_failure(self):
        broken = "@echo off\r\nif exist \"x\" (\r\n    echo band (hozir). Qayta\r\n) else (\r\n    echo ok\r\n)\r\n"
        self.assertTrue(any("қавс" in p for p in lint(broken)))
        fixed = broken.replace("band (hozir).", "band ^(hozir^).")
        self.assertEqual(lint(fixed), [])
        self.assertTrue(lint("goto :yoq\r\n"))


if __name__ == "__main__":
    unittest.main()
