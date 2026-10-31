# -*- coding: utf-8 -*-
import unittest

import _setup  # noqa: F401
from commands import Command, CommandParser, norm, parse_number, tokens_of


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
        cases = {
            "5": 5, "beshinchi slayd": 5, "birinchi": 1, "ikkinchi": 2, "uchinchi": 3,
            "to'rtinchi": 4, "oltinchi": 6, "yettinchi": 7, "sakkizinchi": 8, "o'ninchi": 10,
            "yigirmanchi": 20, "yigirma besh": 25, "o'n besh": 15, "пятый": 5,
            "двадцать три": 23, "third": 3, "twenty one": 21, "12": 12,
            "beshta": 5, "ikkita": 2, "bitta": 1,
        }
        for text, expected in cases.items():
            self.assertEqual(self.p(text), expected, text)

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
        self.assertIsNone(self.intent("oldingo"))  # иккаласига тенг яқин — ҳеч нима

    def test_other_commands(self):
        cases = {
            "boshidan": "first", "бошидан": "first", "qaytadan": "first",
            "oxirgi slayd": "last", "охирги": "last",
            "qora ekran": "black", "қора экран": "black", "oq ekran": "white",
            "ekranni och": "unblank", "ko'rsat": "unblank",
            "tinglama": "pause_listen", "tingla": "resume_listen",
            "boshla": "start", "chiqish": "exit", "чиқиш": "exit", "tugat": "exit",
            "yordam": "help", "holat": "status", "vaqt": "status", "izoh": "notes",
            "mundarija": "outline", "avtomatik": "auto_on", "qolda": "auto_off",
            "avtomatikni o'chir": "auto_off",
        }
        for text, expected in cases.items():
            self.assertEqual(self.intent(text), expected, text)

    def test_goto(self):
        for text, number in [("5 slayd", 5), ("slayd 7", 7), ("beshinchi slayd", 5),
                             ("5-slaydga o'tish", 5), ("10 слайд", 10),
                             ("birinchi slayd", 1), ("go to slide 3", 3)]:
            self.assertEqual(self.parser.parse([text]), Command("goto", number), text)

    def test_relative_jump(self):
        self.assertEqual(self.parser.parse(["2 slayd oldinga"]), Command("jump", 2))
        self.assertEqual(self.parser.parse(["3 slayd orqaga"]), Command("jump", -3))
        self.assertEqual(self.parser.parse(["ikkita slayd ortga"]), Command("jump", -2))

    def test_auto_seconds(self):
        self.assertEqual(self.parser.parse(["avtomatik 20 soniya"]), Command("auto_on", 20))
        self.assertEqual(self.parser.parse(["avto"]), Command("auto_on", None))

    def test_ordinary_speech_is_ignored(self):
        for text in [
            "bugun biz ekologiya haqida gaplashamiz",
            "keyin ko'rsataman",
            "bu juda muhim masala",
            "men chiqish haqida gapirmoqchiman",
            "keyingi slaydda biz natijalarni ko'ramiz",
            "5 ta misol keltiraman",
            "keyingi misol", "keyingi slaydda ko'ramiz", "5 slaydda ko'ramiz",
            "olti oy davomida", "davom etamiz", "oxirgi yillarda",
            "qora rangli", "oq rang bilan", "следующий вопрос",
            "vaqt yetarli emas", "izoh yozing", "unk unk keyingi",
            "",
        ]:
            self.assertIsNone(self.intent(text), text)

    def test_polite_filler_words_are_allowed(self):
        for text, expected in [("keyingi slaydga o'tkaz", "next"),
                               ("iltimos keyingisi", "next"),
                               ("next slide please", "next"),
                               ("следующий слайд", "next"),
                               ("oldingi slaydga qayt", "prev"),
                               ("black screen", "black")]:
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

    def test_vocabulary_and_coverage(self):
        vocabulary = self.parser.vocabulary()
        for word in ["keyingi", "slayd", "besh", "beshinchi", "ikkinchi", "oldinga", "soniya", "unk"][:7]:
            self.assertIn(word, vocabulary)
        coverage = self.parser.coverage({"keyingi", "keyingisi", "slayd"})
        self.assertEqual(coverage["next"], ["keyingi", "keyingisi"])
        self.assertEqual(coverage["prev"], [])

    def test_at_does_not_affect_equality(self):
        self.assertEqual(Command("next", None, 12.5), Command("next"))


if __name__ == "__main__":
    unittest.main()
