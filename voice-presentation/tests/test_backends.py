# -*- coding: utf-8 -*-
"""PowerPointBackend мантиғи — PowerPoint объект модели (OM) номлари билан ясалган сохта COM орқали.

Бу ҳақиқий PowerPoint'ни ўрнини босмайди: у фақат бизнинг назорат оқимимизни (охирги слайддаги
ҳимоя, яширин слайдлар, қора экран, анимациялар) текширади. Атрибут номлари Microsoft
PowerPoint OM ҳужжатига мос.
"""
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import _setup  # noqa: F401
import backends
from backends import KeyboardBackend, PowerPointBackend


class FakeView:
    def __init__(self, pres):
        self.pres = pres
        self.State = 1
        self.index, self.click = 1, 0

    @property
    def Slide(self):
        return SimpleNamespace(SlideIndex=self.index)

    def GetClickIndex(self):
        return self.click

    def GetClickCount(self):
        return self.pres.clicks[self.index - 1]

    def Next(self):
        if self.click < self.GetClickCount():
            self.click += 1
        elif self.index < self.pres.count:
            self.index, self.click = self.index + 1, 0
        else:
            self.State = 5  # ppSlideShowDone: охирги слайддан кейин шоу ёпилади

    def Previous(self):
        if self.click > 0:
            self.click -= 1
        elif self.index > 1:
            self.index, self.click = self.index - 1, 0

    def First(self):
        self.index, self.click = 1, 0

    def Last(self):
        self.index, self.click = self.pres.count, 0

    def GotoSlide(self, number, reset=None):
        self.index, self.click = number, 0

    def Exit(self):
        self.State = 5


class FakeShape:
    def __init__(self, text, body=True):
        self.PlaceholderFormat = SimpleNamespace(Type=2 if body else 1)
        self.HasTextFrame = True
        self.TextFrame = SimpleNamespace(TextRange=SimpleNamespace(Text=text))


class FakeSlide:
    def __init__(self, title, notes, hidden):
        self.SlideShowTransition = SimpleNamespace(Hidden=-1 if hidden else 0)
        self.NotesPage = SimpleNamespace(Shapes=[FakeShape("слайд", body=False), FakeShape(notes)])
        self.Shapes = SimpleNamespace(
            HasTitle=-1 if title else 0,
            Title=SimpleNamespace(TextFrame=SimpleNamespace(TextRange=SimpleNamespace(Text=title))))


class FakePresentation:
    def __init__(self, count=4, clicks=None, hidden=(), titles=None):
        self.count = count
        self.clicks = clicks or [0] * count
        titles = titles or ["Слайд %d" % i for i in range(1, count + 1)]
        self._slides = [FakeSlide(titles[i], "Изоҳ %d" % (i + 1), (i + 1) in hidden) for i in range(count)]
        self.Slides = SimpleNamespace(Count=count, Item=lambda i: self._slides[i - 1])
        self.settings_applied = {}
        self.SlideShowWindow = None
        self.Saved = 0
        self.closed = False
        pres = self

        class Settings:
            def Run(self_inner):
                pres.SlideShowWindow = SimpleNamespace(View=FakeView(pres), Activate=lambda: None)

            def __setattr__(self_inner, name, value):
                pres.settings_applied[name] = value
                object.__setattr__(self_inner, name, value)

        self.SlideShowSettings = Settings()

    def Close(self):
        self.closed = True


class FakeApp:
    def __init__(self, pres, open_presentations=0):
        self.pres = pres
        self.opened = []
        self.Visible = False
        self.AutomationSecurity = None
        self.quit_called = False
        self._open = open_presentations
        outer = self

        class Presentations:
            @property
            def Count(self_inner):  # ёпилган презентация ҳисобдан чиқади (ҳақиқий PowerPoint каби)
                return outer._open + (len(outer.opened) if not outer.pres.closed else 0)

            def Open(self_inner, path, readonly, untitled, with_window):
                outer.opened.append((path, readonly, untitled, with_window))
                return outer.pres

        self.Presentations = Presentations()
        self.ActivePresentation = pres

    def Quit(self):
        self.quit_called = True


def make_backend(pres=None, open_presentations=0):
    pres = pres or FakePresentation()
    app = FakeApp(pres, open_presentations)
    client = SimpleNamespace(Dispatch=lambda name: app)
    with mock.patch.dict(sys.modules, {"win32com": SimpleNamespace(client=client),
                                       "win32com.client": client}):
        backend = PowerPointBackend()
    return backend, app, pres


class PowerPointBackendTests(unittest.TestCase):
    def test_open_is_read_only_without_macros_and_runs_manual_show(self):
        backend, app, pres = make_backend()
        backend.open("E:\\presentation.pptx")
        self.assertEqual(app.opened, [("E:\\presentation.pptx", -1, 0, -1)])  # ReadOnly, WithWindow
        self.assertEqual(app.AutomationSecurity, 3)   # макроссиз
        self.assertTrue(app.Visible)
        self.assertFalse(backend.is_running())
        backend.start()
        self.assertTrue(backend.is_running())
        self.assertEqual(pres.settings_applied, {"ShowType": 1, "RangeType": 1, "AdvanceMode": 1})
        self.assertEqual(backend.position(), (1, 4))

    def started(self, **kwargs):
        backend, app, pres = make_backend(FakePresentation(**kwargs))
        backend.open("x.pptx")
        backend.start()
        return backend, app, pres

    def test_next_never_ends_the_show_on_the_last_slide(self):
        backend, _, pres = self.started(count=3)
        self.assertTrue(backend.next())
        self.assertTrue(backend.next())
        self.assertEqual(backend.position(), (3, 3))
        self.assertFalse(backend.next())          # охирги слайд: Next() чақирилмайди
        self.assertTrue(backend.is_running())
        self.assertEqual(backend.position(), (3, 3))

    def test_animations_on_last_slide_are_still_reachable(self):
        backend, _, _ = self.started(count=2, clicks=[0, 2])
        backend.next()
        self.assertTrue(backend.next())           # 1-анимация
        self.assertTrue(backend.next())           # 2-анимация
        self.assertFalse(backend.next())          # энди ҳақиқатан охири
        self.assertTrue(backend.is_running())

    def test_hidden_trailing_slides_are_not_part_of_the_show(self):
        backend, _, _ = self.started(count=4, hidden=(4,))
        backend.goto(3)
        self.assertFalse(backend.next())          # 4-слайд яширин: 3-слайд охирги
        self.assertTrue(backend.is_running())

    def test_prev_reports_whether_it_moved(self):
        backend, _, _ = self.started(count=3)
        self.assertFalse(backend.prev())
        backend.next()
        self.assertTrue(backend.prev())
        self.assertEqual(backend.position(), (1, 3))

    def test_goto_first_last(self):
        backend, _, _ = self.started(count=5)
        self.assertTrue(backend.goto(4))
        self.assertEqual(backend.position(), (4, 5))
        self.assertFalse(backend.goto(9))
        self.assertFalse(backend.goto(0))
        backend.last()
        self.assertEqual(backend.position(), (5, 5))
        backend.first()
        self.assertEqual(backend.position(), (1, 5))

    def test_black_white_and_navigation_restores_the_slide(self):
        backend, _, pres = self.started(count=3)
        backend.black()
        self.assertEqual(pres.SlideShowWindow.View.State, 3)
        backend.next()
        self.assertEqual(pres.SlideShowWindow.View.State, 1)
        self.assertEqual(backend.position(), (2, 3))
        backend.white()
        self.assertEqual(pres.SlideShowWindow.View.State, 4)
        backend.unblank()
        self.assertEqual(pres.SlideShowWindow.View.State, 1)

    def test_notes_and_titles(self):
        backend, _, _ = self.started(count=3, titles=["Кириш", "", "Хулоса"])
        backend.goto(2)
        self.assertEqual(backend.notes(), "Изоҳ 2")      # фақат body placeholder
        self.assertEqual(backend.titles(), ["Кириш", "", "Хулоса"])

    def test_show_ended_by_user_is_detected_and_can_restart(self):
        backend, _, pres = self.started(count=3)
        pres.SlideShowWindow.View.Exit()               # фойдаланувчи Esc босди
        self.assertFalse(backend.is_running())
        self.assertIsNone(backend.position())
        backend.start()
        self.assertTrue(backend.is_running())
        self.assertEqual(backend.position(), (1, 3))

    def test_close_closes_our_presentation_and_quits_our_powerpoint(self):
        backend, app, pres = self.started()
        backend.close()
        self.assertTrue(pres.closed)
        self.assertEqual(pres.Saved, -1)               # "сақлайсизми?" саволи чиқмайди
        self.assertTrue(app.quit_called)

    def test_close_leaves_foreign_powerpoint_windows_alone(self):
        backend, app, pres = make_backend(open_presentations=1)
        backend.open("x.pptx")
        backend.start()
        backend.close()
        self.assertTrue(pres.closed)
        self.assertFalse(app.quit_called)              # фойдаланувчининг бошқа презентацияси бор

    def test_attach_does_not_close_the_users_presentation(self):
        backend, app, pres = make_backend(open_presentations=1)
        backend.attach()
        backend.start()
        backend.close()
        self.assertFalse(pres.closed)
        self.assertFalse(app.quit_called)
        self.assertFalse(backend.is_running())         # фақат шоу ёпилди

    def test_missing_pywin32_raises_import_error(self):
        with mock.patch.dict(sys.modules, {"win32com": None, "win32com.client": None}):
            with self.assertRaises(ImportError):
                PowerPointBackend()


class KeyboardBackendTests(unittest.TestCase):
    def setUp(self):
        self.keys = []
        patches = [mock.patch.object(backends.winkeys, "available", return_value=True),
                   mock.patch.object(backends.winkeys, "press", side_effect=self.keys.append),
                   mock.patch.object(backends.winkeys, "type_digits",
                                     side_effect=lambda d: self.keys.append("digits:" + d))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.backend = KeyboardBackend(open_delay=0)

    def test_navigation_keys(self):
        self.backend.start()
        self.backend.next()
        self.backend.prev()
        self.backend.first()
        self.backend.last()
        self.backend.goto(12)
        self.backend.exit_show()
        self.assertEqual(self.keys, ["f5", "right", "left", "home", "end", "digits:12", "enter", "esc"])
        self.assertFalse(self.backend.is_running())

    def test_blank_toggles_back_before_navigating(self):
        self.backend.black()
        self.backend.next()
        self.backend.white()
        self.backend.unblank()
        self.assertEqual(self.keys, ["b", "b", "right", "w", "w"])

    def test_not_available_off_windows(self):
        with mock.patch.object(backends.winkeys, "available", return_value=False):
            with self.assertRaises(OSError):
                KeyboardBackend()


if __name__ == "__main__":
    unittest.main()
