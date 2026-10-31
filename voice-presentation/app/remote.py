# -*- coding: utf-8 -*-
"""Телефон пульти ва REST API.

Презентацияни локал тармоқдан бошқариш: телефон браузерида катта тугмалар саҳифаси ва
IFTTT / вебхук / скрипт орқали чақириладиган оддий HTTP API. Фақат Python стандарт кутубхонаси.

  GET  /?t=ТОКЕН                          телефон саҳифаси
  GET  /api/status?t=ТОКЕН                ҳолат (JSON)
  GET|POST /api/command/<номи>?t=ТОКЕН[&n=5]   буйруқ юбориш (next, prev, goto ...)
  POST /api/command  {"command": "goto", "n": 5}  (токен: ?t= ёки X-Token ҳедери)

Хавфсизлик: ёқилмаса ишламайди; ҳар сессияда тасодифий токен; токенсиз ҳамма сўров рад этилади;
сўровлар сони чекланган. Трафик шифрланмайди (HTTP) — фақат ишончли Wi-Fi'да ишлатинг.
Буйруқлар асосий оқимга навбат орқали берилади (PowerPoint COM фақат асосий оқимда ишлайди).
"""
import hmac
import json
import logging
import secrets
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Deque, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from commands import Command

log = logging.getLogger("voicepres")

# Масофадан рухсат этилган буйруқлар. Рақам талаб қиладиганлари алоҳида.
SIMPLE_COMMANDS = {
    "next", "prev", "first", "last", "black", "white", "unblank", "start", "exit",
    "pause_listen", "resume_listen", "mark", "to_mark", "auto_off", "status", "notes",
    "outline", "report", "help",
}
NUMERIC_COMMANDS = {"goto": (1, 9999), "jump": (-999, 999), "auto_on": (2, 3600)}


def new_token() -> str:
    return secrets.token_hex(6)


def lan_address() -> str:
    """Шу компьютернинг локал тармоқдаги манзили (пакет юборилмайди)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


class SessionState:
    """HTTP оқимлари ўқийдиган ҳолат нусхаси (асосий оқим янгилайди)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, object] = {"slide": None, "total": None, "elapsed": 0,
                                         "listening": True, "auto": None, "bookmark": None}
        self._messages: Deque[str] = deque(maxlen=12)

    def update(self, **values) -> None:
        with self._lock:
            self._data.update(values)

    def add_message(self, text: str) -> None:
        with self._lock:
            self._messages.append(text)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._data, messages=list(self._messages))


PAGE = """<!doctype html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>Презентация пульти</title><style>
:root{color-scheme:light dark;--bg:#fff;--fg:#111;--b:#e9edf3;--a:#2b7de9}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--fg:#eee;--b:#242a33}}
body{margin:0;font:18px system-ui,sans-serif;background:var(--bg);color:var(--fg);padding:12px}
#st{font-size:34px;text-align:center;margin:8px 0}#sub{text-align:center;opacity:.7;min-height:1.3em}
.g{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:10px 0}
button{font:inherit;padding:18px 6px;border:0;border-radius:12px;background:var(--b);color:var(--fg)}
button.p{background:var(--a);color:#fff;font-size:26px;padding:34px 6px}button:active{transform:scale(.97)}
input{font:inherit;width:100%;box-sizing:border-box;padding:14px;border-radius:12px;border:1px solid #8886;
background:var(--bg);color:var(--fg)}#log{font-size:14px;opacity:.75;white-space:pre-wrap}
</style></head><body>
<div id="st">—</div><div id="sub"></div>
<div class="g"><button onclick="c('prev')">◀ Олдинги</button><button class="p" onclick="c('next')">Кейинги ▶</button></div>
<div class="g"><button onclick="c('first')">⏮ Бошидан</button><button onclick="c('last')">Охирги ⏭</button>
<button onclick="c('black')">Қора экран</button><button onclick="c('white')">Оқ экран</button>
<button onclick="c('unblank')">Экранни қайтар</button><button onclick="c('notes')">Изоҳ</button>
<button onclick="c('mark')">Белгила</button><button onclick="c('to_mark')">Белгига қайт</button>
<button onclick="c('pause_listen')">Микрофон ⏸</button><button onclick="c('resume_listen')">Микрофон ▶</button></div>
<div class="g"><input id="n" type="number" inputmode="numeric" placeholder="Слайд рақами">
<button onclick="c('goto',document.getElementById('n').value)">Ўтиш</button></div>
<div class="g"><button onclick="c('auto_on',15)">Авто 15 с</button><button onclick="c('auto_off')">Қўлда</button>
<button onclick="if(confirm('Намойишни ёпасизми?'))c('exit')">Чиқиш</button><button onclick="c('report')">Ҳисобот</button></div>
<div id="log"></div><script>
const T=new URLSearchParams(location.search).get('t')||'';
async function c(name,n){try{await fetch('/api/command/'+name+'?t='+encodeURIComponent(T)+(n!==undefined?'&n='+encodeURIComponent(n):''),{method:'POST'});}catch(e){}poll();}
function fmt(s){s=Math.floor(s||0);return String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0');}
async function poll(){try{const r=await fetch('/api/status?t='+encodeURIComponent(T));if(!r.ok){document.getElementById('st').textContent='Токен нотўғри';return;}
const d=await r.json();document.getElementById('st').textContent=d.slide?d.slide+' / '+d.total:'—';
document.getElementById('sub').textContent=fmt(d.elapsed)+(d.auto?' · авто '+d.auto+' с':'')+(d.listening?'':' · микрофон ⏸')+(d.bookmark?' · белги '+d.bookmark:'');
document.getElementById('log').textContent=(d.messages||[]).slice().reverse().join('\\n');}catch(e){}}
poll();setInterval(poll,1000);</script></body></html>"""


class RemoteServer:
    def __init__(self, submit: Callable[[Command], None], state: SessionState, token: str,
                 host: str = "0.0.0.0", port: int = 8765, rate_per_second: int = 20):
        self.submit, self.state, self.token = submit, state, token
        self.host, self.port = host, port
        self.rate = rate_per_second
        self._hits: Deque[float] = deque()
        self._server: Optional[ThreadingHTTPServer] = None

    # -- ички ---------------------------------------------------------------------
    def _allowed_rate(self) -> bool:
        now = time.monotonic()
        while self._hits and now - self._hits[0] > 1.0:
            self._hits.popleft()
        if len(self._hits) >= self.rate:
            return False
        self._hits.append(now)
        return True

    def build_command(self, name: str, raw_number: Optional[str]) -> Optional[Command]:
        """Исталган номни тўғри Command'га айлантиради (нотўғри бўлса None)."""
        if name in SIMPLE_COMMANDS:
            return Command(name, manual=True)
        if name in NUMERIC_COMMANDS:
            low, high = NUMERIC_COMMANDS[name]
            try:
                number = int(raw_number)
            except (TypeError, ValueError):
                if name == "auto_on":
                    return Command(name, None, manual=True)
                return None
            return Command(name, number, manual=True) if low <= number <= high else None
        return None

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, fmt, *args):  # консолни тўлдирмаслик учун
                log.debug("remote: " + fmt, *args)

            def _send(self, code: int, body: bytes, kind: str = "application/json; charset=utf-8") -> None:
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, payload: dict) -> None:
                self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

            def _authorized(self, query: dict) -> bool:
                given = (query.get("t", [""])[0] or self.headers.get("X-Token", "")
                         or self.headers.get("Authorization", "").replace("Bearer ", "", 1))
                return hmac.compare_digest(given.encode("utf-8"), server.token.encode("utf-8"))

            def _route(self, method: str) -> None:
                url = urlparse(self.path)
                query = parse_qs(url.query)
                if not server._allowed_rate():
                    return self._json(429, {"ok": False, "error": "too many requests"})
                if not self._authorized(query):
                    return self._json(401, {"ok": False, "error": "token required"})
                path = url.path.rstrip("/") or "/"
                if method == "GET" and path == "/":
                    return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                if method == "GET" and path == "/api/status":
                    return self._json(200, dict(server.state.snapshot(), ok=True))
                if path.startswith("/api/command") and method in ("GET", "POST"):
                    name, number = path[len("/api/command"):].strip("/"), (query.get("n") or [None])[0]
                    if method == "POST" and not name:
                        try:
                            length = min(int(self.headers.get("Content-Length", "0")), 1024)
                            body = json.loads(self.rfile.read(length) or b"{}")
                            name, number = str(body.get("command", "")), body.get("n")
                        except (ValueError, TypeError):
                            return self._json(400, {"ok": False, "error": "bad json"})
                    command = server.build_command(name, None if number is None else str(number))
                    if command is None:
                        return self._json(400, {"ok": False, "error": "unknown command or bad number",
                                                "commands": sorted(SIMPLE_COMMANDS | set(NUMERIC_COMMANDS))})
                    server.submit(command)
                    return self._json(200, {"ok": True, "command": name})
                self._json(404, {"ok": False, "error": "not found"})

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

        return Handler

    # -- ташқи ---------------------------------------------------------------------
    def start(self) -> int:
        """Серверни ишга туширади ва ҳақиқий портни қайтаради (port=0 бўлса — эркин порт)."""
        self._server = ThreadingHTTPServer((self.host, self.port), self._handler())
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=lambda: self._server.serve_forever(poll_interval=0.05), daemon=True).start()
        return self.port

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    def urls(self) -> List[str]:
        host = lan_address() if self.host in ("0.0.0.0", "") else self.host
        return ["http://%s:%d/?t=%s" % (host, self.port, self.token)]
