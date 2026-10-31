# -*- coding: utf-8 -*-
"""Слайдларни бошқариш усуллари.

  * PowerPointBackend — PowerPoint'ни COM орқали бошқаради (энг ишончли: ойна
    фокусига боғлиқ эмас, слайд рақамини билади, охирги слайдда шоу ёпилиб
    кетишининг олдини олади).
  * KeyboardBackend   — F5, ←, → тугмаларини босади (LibreOffice, WPS, .odp
    ва PowerPoint COM ишламаган ҳолатлар учун заҳира).
  * DryRunBackend     — ҳеч нима очмайди, фақат ҳолатни ҳисоблайди
    (микрофонни синаш ва тестлар учун).
"""
import os
import time
from typing import Optional, Tuple

PP_SLIDESHOW_RUNNING = 1
PP_SLIDESHOW_BLACK = 3
PP_SLIDESHOW_WHITE = 4
PP_SLIDESHOW_DONE = 5


class Backend:
    name = "?"

    def open(self, path) -> None: ...
    def start(self) -> None: ...
    def is_running(self) -> bool: return True
    def position(self) -> Optional[Tuple[int, int]]: return None
    def next(self) -> bool: ...        # False — ҳаракат бўлмади (охирги слайд)
    def prev(self) -> bool: ...        # False — ҳаракат бўлмади (биринчи слайд)
    def first(self) -> None: ...
    def last(self) -> None: ...
    def goto(self, number: int) -> bool: ...   # False — бундай слайд йўқ
    def black(self) -> None: ...
    def white(self) -> None: ...
    def unblank(self) -> None: ...
    def exit_show(self) -> None: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
class DryRunBackend(Backend):
    """Ҳеч нима очмайди. Микрофон ва буйруқларни синаш учун."""
    name = "Синов режими (презентациясиз)"

    def __init__(self, total: int = 12):
        self.total = total
        self.current = 1
        self.running = True
        self.blank: Optional[str] = None

    def start(self):
        self.running = True
        self.current = 1

    def is_running(self):
        return self.running

    def position(self):
        return (self.current, self.total) if self.running else None

    def next(self):
        self.blank = None
        if self.current >= self.total:
            return False
        self.current += 1
        return True

    def prev(self):
        self.blank = None
        if self.current <= 1:
            return False
        self.current -= 1
        return True

    def first(self): self.blank = None; self.current = 1
    def last(self): self.blank = None; self.current = self.total

    def goto(self, number):
        if not 1 <= number <= self.total:
            return False
        self.blank = None
        self.current = number
        return True

    def black(self): self.blank = "black"
    def white(self): self.blank = "white"
    def unblank(self): self.blank = None
    def exit_show(self): self.running = False


# ---------------------------------------------------------------------------
class PowerPointBackend(Backend):
    """Microsoft PowerPoint (Windows, pywin32 керак)."""
    name = "Microsoft PowerPoint (COM)"

    def __init__(self):
        import win32com.client  # ImportError бўлса — чақирувчи заҳирага ўтади
        self.app = win32com.client.Dispatch("PowerPoint.Application")
        self.pres = None
        self._owns_app = False
        self._opened_by_us = False

    # -- очиш / бошлаш ----------------------------------------------------------
    def open(self, path):
        self._owns_app = self.app.Presentations.Count == 0
        self.app.Visible = True
        try:
            self.app.AutomationSecurity = 3  # msoAutomationSecurityForceDisable: макросларсиз
        except Exception:
            pass
        # FileName, ReadOnly=msoTrue, Untitled=msoFalse, WithWindow=msoTrue
        self.pres = self.app.Presentations.Open(str(path), -1, 0, -1)
        self._opened_by_us = True

    def attach(self):
        """Аллақачон очиқ турган презентацияни бошқариш."""
        self.pres = self.app.ActivePresentation

    def start(self):
        settings = self.pres.SlideShowSettings
        for attr, value in (("ShowType", 1),       # доклад режими
                            ("RangeType", 1),      # барча слайдлар
                            ("AdvanceMode", 1)):   # фақат қўлда (овоз билан) ўтказиш
            try:
                setattr(settings, attr, value)
            except Exception:
                pass
        settings.Run()
        try:
            self.pres.SlideShowWindow.Activate()
        except Exception:
            pass

    # -- ҳолат ------------------------------------------------------------------
    def _view(self):
        try:
            view = self.pres.SlideShowWindow.View
            return None if view.State == PP_SLIDESHOW_DONE else view
        except Exception:
            return None

    def is_running(self):
        return self._view() is not None

    def position(self):
        view = self._view()
        if view is None:
            return None
        try:
            return view.Slide.SlideIndex, self.pres.Slides.Count
        except Exception:
            return None

    def _last_visible_index(self):
        count = self.pres.Slides.Count
        for index in range(count, 0, -1):
            try:
                hidden = self.pres.Slides.Item(index).SlideShowTransition.Hidden
            except Exception:
                hidden = 0
            if not hidden:
                return index
        return count

    @staticmethod
    def _click_state(view):
        try:
            return view.GetClickIndex(), view.GetClickCount()
        except Exception:
            return 0, 0

    def _snapshot(self, view):
        return view.Slide.SlideIndex, self._click_state(view)[0]

    # -- ҳаракатлар -------------------------------------------------------------
    def next(self):
        view = self._view()
        self._unblank_view(view)
        click, clicks = self._click_state(view)
        if view.Slide.SlideIndex >= self._last_visible_index() and click >= clicks:
            return False  # охирги слайдда Next() шоуни ёпиб юборарди
        view.Next()
        return True

    def prev(self):
        view = self._view()
        self._unblank_view(view)
        before = self._snapshot(view)
        view.Previous()
        return self._snapshot(view) != before

    def first(self):
        view = self._view()
        self._unblank_view(view)
        view.First()

    def last(self):
        view = self._view()
        self._unblank_view(view)
        view.Last()

    def goto(self, number):
        view = self._view()
        if not 1 <= number <= self.pres.Slides.Count:
            return False
        self._unblank_view(view)
        view.GotoSlide(number)
        return True

    def black(self): self._view().State = PP_SLIDESHOW_BLACK
    def white(self): self._view().State = PP_SLIDESHOW_WHITE
    def unblank(self): self._unblank_view(self._view())

    @staticmethod
    def _unblank_view(view):
        if view.State in (PP_SLIDESHOW_BLACK, PP_SLIDESHOW_WHITE):
            view.State = PP_SLIDESHOW_RUNNING

    def exit_show(self):
        view = self._view()
        if view is not None:
            view.Exit()

    def close(self):
        if self.pres is None:
            return
        try:
            self.exit_show()
        except Exception:
            pass
        if not self._opened_by_us:
            return  # фойдаланувчининг ўз презентациясини ёпмаймиз
        try:
            self.pres.Saved = -1
            self.pres.Close()
            if self._owns_app and self.app.Presentations.Count == 0:
                self.app.Quit()
        except Exception:
            pass


# ---------------------------------------------------------------------------
class KeyboardBackend(Backend):
    """Тугмалар симуляцияси: энг содда, лекин фаол ойнага боғлиқ."""
    name = "Клавиатура (F5, стрелкалар)"

    def __init__(self, open_delay: float = 5.0):
        import pyautogui
        pyautogui.FAILSAFE = False  # сичқонча бурчакда турса ҳам исиб кетмасин
        pyautogui.PAUSE = 0.05
        self.pg = pyautogui
        self.open_delay = open_delay
        self.running = False
        self._blank: Optional[str] = None

    def open(self, path):
        os.startfile(str(path))  # Windows: файлни стандарт дастурда очади
        time.sleep(self.open_delay)

    def start(self):
        self.pg.press("f5")
        self.running = True

    def is_running(self):
        return self.running

    def _unblank_keys(self):
        if self._blank:
            self.pg.press(self._blank)
            self._blank = None

    def next(self): self._unblank_keys(); self.pg.press("right"); return True
    def prev(self): self._unblank_keys(); self.pg.press("left"); return True
    def first(self): self._unblank_keys(); self.pg.press("home")
    def last(self): self._unblank_keys(); self.pg.press("end")

    def goto(self, number):
        self._unblank_keys()
        self.pg.write(str(number))
        self.pg.press("enter")
        return True

    def black(self):
        self._unblank_keys()
        self.pg.press("b")
        self._blank = "b"

    def white(self):
        self._unblank_keys()
        self.pg.press("w")
        self._blank = "w"

    def unblank(self): self._unblank_keys()

    def exit_show(self):
        self._unblank_keys()
        self.pg.press("esc")
        self.running = False
