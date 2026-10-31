# -*- coding: utf-8 -*-
"""Клавиатура тугмаларини босиш: Windows SendInput (ташқи кутубхонасиз).

PowerPoint COM ишламаган ҳолатларда (LibreOffice, WPS, PowerPoint Viewer)
шу орқали F5, ←, → ва бошқа тугмалар босилади.
"""
import ctypes
import sys
import time

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

VK = {
    "right": 0x27, "left": 0x25, "up": 0x26, "down": 0x28,
    "home": 0x24, "end": 0x23, "pgup": 0x21, "pgdn": 0x22,
    "esc": 0x1B, "enter": 0x0D, "space": 0x20, "shift": 0x10,
    "f5": 0x74, "b": 0x42, "w": 0x57, "period": 0xBE,
}
# Бу тугмаларда "extended" белгиси керак, акс ҳолда NumLock'га қараб рақам тугмаси босилади
EXTENDED = {"right", "left", "up", "down", "home", "end", "pgup", "pgdn"}

# Аниқ ўлчамли турлар (ctypes.wintypes ўрнига): Windows'да DWORD=4 байт, лекин Linux'да 8.
# Шунда тузилма ҳамма жойда бир хил (64-бит: 40 байт) бўлади ва уни текшириш мумкин.
LONG, DWORD, WORD = ctypes.c_int32, ctypes.c_uint32, ctypes.c_uint16
ULONG_PTR = ctypes.c_size_t  # кўрсаткич ўлчамидаги бутун сон


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", LONG), ("dy", LONG),
                ("mouseData", DWORD), ("dwFlags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", WORD), ("wScan", WORD),
                ("dwFlags", DWORD), ("time", DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD),
                ("wParamH", WORD)]


class _InputUnion(ctypes.Union):
    # MOUSEINPUT union'ни тўғри ўлчамга келтириши учун шарт (SendInput ўлчамни текширади)
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", DWORD), ("u", _InputUnion)]


def available() -> bool:
    return sys.platform == "win32"


def _key_events(vk: int, extended: bool):
    flags = KEYEVENTF_EXTENDEDKEY if extended else 0
    down, up = INPUT(), INPUT()
    for event, extra in ((down, 0), (up, KEYEVENTF_KEYUP)):
        event.type = INPUT_KEYBOARD
        event.ki = KEYBDINPUT(vk, 0, flags | extra, 0, 0)
    return down, up


def _send(vk: int, extended: bool = False) -> None:
    if not available():
        raise OSError("Тугмаларни босиш фақат Windows'да ишлайди")
    events = (INPUT * 2)(*_key_events(vk, extended))
    sent = ctypes.windll.user32.SendInput(2, ctypes.byref(events), ctypes.sizeof(INPUT))
    if sent != 2:
        raise OSError("SendInput ишламади (хато коди %d). Презентация "
                      "администратор ҳуқуқида очилган бўлиши мумкин." % ctypes.GetLastError())


def press(name: str) -> None:
    """Тугмани босади: press("right"), press("f5"), press("b")."""
    _send(VK[name], name in EXTENDED)
    time.sleep(0.03)


def type_digits(digits: str) -> None:
    """Рақамларни кетма-кет босади (слайд рақамини териш учун)."""
    if not digits.isascii() or not digits.isdigit():
        raise ValueError("Фақат рақам бўлиши керак: %r" % digits)
    for char in digits:
        _send(ord(char))
        time.sleep(0.03)
