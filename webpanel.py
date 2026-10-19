# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import re
import socket
import threading

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
    from urllib.parse import parse_qs, urlparse
except ImportError:
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
    from SocketServer import ThreadingMixIn
    from urlparse import parse_qs, urlparse

from . import configs
from . import engine
from .settings import get_settings

_server = [None]

PAGE = u"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Proton VPN</title><style>
:root{--bg:#0f0e17;--card:#1b1a27;--line:#2c2a3d;--txt:#ecebf5;--mut:#9a97b0;--acc:#6d4aff;--ok:#2fd58a;--bad:#ff5c7a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:20px 16px 40px}h1{font-size:22px;margin:0 0 16px}h1 b{color:var(--acc)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:14px}
#st{font-size:18px;font-weight:600}.ok{color:var(--ok)}.bad{color:var(--bad)}.mut{color:var(--mut);font-size:13px}
button,.btn{background:var(--acc);color:#fff;border:0;border-radius:10px;padding:9px 14px;font:inherit;cursor:pointer}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--txt)}button.danger{background:var(--bad)}
input[type=file]{color:var(--mut);max-width:100%%}textarea{width:100%%;min-height:120px;background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:10px;padding:8px;font:12px monospace}
input[type=text]{background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:10px;padding:8px;width:100%%}
table{width:100%%;border-collapse:collapse}td{padding:8px 4px;border-top:1px solid var(--line)}td:last-child{text-align:right;white-space:nowrap}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.tag{font-size:11px;border:1px solid var(--line);border-radius:6px;padding:1px 6px;color:var(--mut);margin-left:4px}
</style></head><body><main>
<h1><b>Proton</b> VPN &middot; Enigma2</h1>
<div class="card"><div id="st">...</div><div class="mut" id="det"></div>
<div class="row" style="margin-top:10px"><button onclick="act('fastest')">%(fastest)s</button><button class="ghost" onclick="act('disconnect')">%(disc)s</button></div></div>
<div class="card"><b>%(upload)s</b><p class="mut">%(hint)s</p>
<form id="up" class="row"><input type="file" name="f" multiple accept=".conf,.ovpn,.zip"><button>%(send)s</button></form>
<details style="margin-top:10px"><summary class="mut">%(paste)s</summary>
<input type="text" id="pn" placeholder="NL-FREE-12.conf" style="margin:8px 0"><textarea id="pt" placeholder="[Interface]&#10;PrivateKey = ..."></textarea>
<button style="margin-top:8px" onclick="paste()">%(send)s</button></details><div id="msg" class="mut" style="margin-top:8px"></div></div>
<div class="card"><b>%(servers)s</b> <span class="mut" id="cnt"></span><table id="tb"></table></div>
</main><script>
function q(u,o){return fetch(u,o).then(function(r){return r.json()})}
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function load(){q('api/status').then(function(s){var e=document.getElementById('st');
 e.textContent=s.connected?'%(con)s: '+s.label:'%(dis)s';e.className=s.connected?'ok':'bad';
 document.getElementById('det').textContent=s.connected?(s.proto+' \\u00b7 '+s.endpoint+(s.killswitch?' \\u00b7 kill switch':'')):(s.killswitch?'kill switch':'')});
 q('api/servers').then(function(l){document.getElementById('cnt').textContent='('+l.length+')';
 document.getElementById('tb').innerHTML=l.map(function(s){return '<tr><td>'+esc(s.title)+' <span class="mut">'+esc(s.id)+'</span>'+(s.free?'<span class="tag">Free</span>':'')+
 '<span class="tag">'+esc(s.proto)+'</span></td><td><button class="ghost" onclick="act(\\'connect\\',\\''+encodeURIComponent(s.file)+'\\')">%(conbtn)s</button> '+
 '<button class="ghost" onclick="del(\\''+encodeURIComponent(s.file)+'\\')">&times;</button></td></tr>'}).join('')})}
function act(a,f){document.getElementById('st').textContent='...';q('api/'+a+(f?'?file='+f:''),{method:'POST'}).then(function(r){if(r.msg)document.getElementById('msg').textContent=r.msg;load()})}
function del(f){if(confirm('Delete?'))q('api/delete?file='+f,{method:'POST'}).then(load)}
function paste(){var n=document.getElementById('pn').value||'server.conf';q('api/paste?name='+encodeURIComponent(n),{method:'POST',body:document.getElementById('pt').value}).then(function(r){document.getElementById('msg').textContent=r.msg;load()})}
document.getElementById('up').onsubmit=function(ev){ev.preventDefault();q('api/upload',{method:'POST',body:new FormData(this)}).then(function(r){document.getElementById('msg').textContent=r.msg;load()})};
load();setInterval(load,5000);
</script></body></html>"""

TXT = {
    "en": {"fastest": "Connect fastest", "disc": "Disconnect", "upload": "Add servers",
           "hint": "WireGuard (.conf), OpenVPN (.ovpn) or a .zip from account.protonvpn.com &rarr; Downloads",
           "send": "Upload", "paste": "or paste a config", "servers": "Servers", "con": "Connected",
           "dis": "Disconnected", "conbtn": "Connect"},
    "tr": {"fastest": "En hızlıya bağlan", "disc": "Bağlantıyı kes", "upload": "Sunucu ekle",
           "hint": "account.protonvpn.com &rarr; Downloads bölümünden WireGuard (.conf), OpenVPN (.ovpn) veya .zip",
           "send": "Yükle", "paste": "veya config yapıştır", "servers": "Sunucular", "con": "Bağlı",
           "dis": "Bağlı değil", "conbtn": "Bağlan"},
}


def _is_lan(ip):
    if ip.startswith("::ffff:"):
        ip = ip[7:]
    if ip in ("127.0.0.1", "::1") or ip.startswith("fe80:") or ip.startswith("fd") or ip.startswith("fc"):
        return True
    try:
        a, b = [int(x) for x in ip.split(".")[:2]]
    except ValueError:
        return False
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168) or (a == 100 and 64 <= b <= 127)


def _multipart(body, ctype):
    m = re.search(r'boundary="?([^";]+)"?', ctype)
    if not m:
        return []
    boundary = b"--" + m.group(1).encode("ascii")
    files = []
    for part in body.split(boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, data = part.split(b"\r\n\r\n", 1)
        fn = re.search(br'filename="([^"]*)"', head)
        if not fn or not fn.group(1):
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        files.append((fn.group(1).decode("utf-8", "replace"), data))
    return files


def _lang():
    from .i18n import lang
    return lang()


class Handler(BaseHTTPRequestHandler):
    server_version = "ProtonVPN-E2"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if not isinstance(body, bytes):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj))

    def _allowed(self):
        if _is_lan(self.client_address[0]):
            return True
        self._send(403, "forbidden", "text/plain")
        return False

    def do_GET(self):
        if not self._allowed():
            return
        u = urlparse(self.path)
        path = u.path.rstrip("/").split("/")[-1]
        if path in ("", "index.html"):
            self._send(200, PAGE % TXT[_lang()], "text/html")
        elif path == "status":
            st = engine.Engine().status()
            st.pop("pid", None)
            self._json(st)
        elif path == "servers":
            lg = _lang()
            self._json([{"file": s.filename, "id": s.short_id(), "title": s.title(lg), "proto": s.proto_label(),
                         "free": s.free} for s in configs.sort_servers(configs.load_servers(), lang=lg)])
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if not self._allowed():
            return
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        path = u.path.rstrip("/").split("/")[-1]
        length = int(self.headers.get("Content-Length") or 0)
        if length > 8 * 1024 * 1024:
            return self._json({"msg": "too large"}, 413)
        body = self.rfile.read(length) if length else b""
        lg = _lang()
        if path == "upload":
            stored = []
            for name, data in _multipart(body, self.headers.get("Content-Type", "")):
                if name.lower().endswith(".zip"):
                    import os
                    import tempfile
                    fd, tmp = tempfile.mkstemp(suffix=".zip")
                    os.write(fd, data)
                    os.close(fd)
                    stored += configs.import_path(tmp)
                    os.remove(tmp)
                else:
                    configs.ensure_dirs()
                    r = configs.store_config(name, data)
                    if r:
                        stored.append(r)
            msg = ("%d config yüklendi" if lg == "tr" else "%d config(s) added") % len(stored)
            self._json({"msg": msg, "stored": stored})
        elif path == "paste":
            configs.ensure_dirs()
            name = (qs.get("name") or ["server.conf"])[0]
            if not name.lower().endswith((".conf", ".ovpn")):
                name += ".conf"
            r = configs.store_config(name, body)
            self._json({"msg": (r or ("geçersiz config" if lg == "tr" else "invalid config"))})
        elif path == "delete":
            configs.delete_config((qs.get("file") or [""])[0])
            self._json({"ok": True})
        elif path == "disconnect":
            threading.Thread(target=engine.Engine().disconnect).start()
            self._json({"ok": True})
        elif path in ("connect", "fastest"):
            fname = (qs.get("file") or [""])[0]

            def job():
                eng = engine.Engine()
                if path == "fastest":
                    t = engine.pick_fastest(engine.all_servers(), eng.settings)
                else:
                    t = engine.find_server(fname)
                if t:
                    eng.connect(t)
            threading.Thread(target=job).start()
            self._json({"msg": "..."})
        else:
            self._send(404, "not found", "text/plain")


class _Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start(port=None):
    if _server[0]:
        return True
    port = int(port or get_settings()["webpanel_port"] or 8091)
    try:
        srv = _Server(("", port), Handler)
    except (socket.error, OSError):
        return False
    t = threading.Thread(target=srv.serve_forever)
    t.daemon = True
    t.start()
    _server[0] = srv
    return True


def stop():
    srv = _server[0]
    if srv:
        _server[0] = None
        threading.Thread(target=srv.shutdown).start()


def running():
    return _server[0] is not None


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except (socket.error, OSError):
        return "box-ip"
    finally:
        s.close()
