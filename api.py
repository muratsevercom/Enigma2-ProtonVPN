# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import json
import os
import subprocess
import time

try:
    from . import configs
except (ValueError, ImportError, SystemError):
    import configs

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
ETC = configs.ETC_DIR
SERVERS_FILE = os.path.join(ETC, "servers.json")
ACCOUNT_FILE = os.path.join(ETC, "account.json")
SESSION_FILE = os.path.join(ETC, "session.json")
CERT_FILE = os.path.join(ETC, "cert.json")
AGENT_FILE = "/var/run/protonvpn/agent.json"
GENERATED_OVPN = "/var/run/protonvpn/proton-api.ovpn"

F_SECURE_CORE, F_TOR, F_P2P, F_STREAMING, F_IPV6 = 1, 2, 4, 8, 16
WG_PORT = 51820
OVPN_PORTS = {"udp": [1194, 4569, 80], "tcp": [443, 7770, 8443]}


def _arch_dirs():
    m = os.uname()[4].lower()
    if m.startswith("mips"):
        return ["mipsel"]
    if m in ("aarch64", "arm64"):
        return ["aarch64", "armv7"]
    if m.startswith("arm"):
        return ["armv7"]
    if m in ("x86_64", "amd64"):
        return ["amd64"]
    return ["armv7", "mipsel", "aarch64"]


def helper():
    p = os.path.join(PLUGIN_DIR, "bin", "protonvpn")
    if os.path.isfile(p):
        if not os.access(p, os.X_OK):
            os.chmod(p, 0o755)
        return p
    for a in _arch_dirs():
        p = os.path.join(PLUGIN_DIR, "bin", a, "protonvpn")
        if os.path.isfile(p):
            os.chmod(p, 0o755)
            return p
    return None


def call(args, stdin=None, timeout=60):
    h = helper()
    if not h:
        return {"ok": False, "code": -1, "error": "helper binary missing for this CPU"}
    try:
        p = subprocess.Popen([h] + list(args), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, close_fds=True)
        data = stdin.encode("utf-8") if stdin is not None and not isinstance(stdin, bytes) else stdin
        out, err = p.communicate(data)
    except OSError as e:
        return {"ok": False, "code": -1, "error": str(e)}
    out = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
    for line in reversed(out.strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    e = err.decode("utf-8", "replace") if isinstance(err, bytes) else (err or "")
    return {"ok": False, "code": -1, "error": (e.strip() or out.strip() or "no answer")[-300:]}


def logged_in():
    try:
        with open(SESSION_FILE) as f:
            s = json.load(f)
        return bool(s.get("uid")) and not s.get("need_2fa")
    except (IOError, OSError, ValueError):
        return False


def login(user, password):
    return call(["login", user], stdin=password + "\n")


def fork_start():
    return call(["fork-start"])


def fork_poll():
    return call(["fork-poll"])


def submit_2fa(code):
    return call(["2fa", code])


def logout():
    _api_cache["mtime"] = None
    return call(["logout"])


def fetch_account():
    return call(["account"])


def account():
    try:
        with open(ACCOUNT_FILE) as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return {}


def fetch_servers():
    r = call(["servers"], timeout=90)
    _api_cache["mtime"] = None
    return r


def servers_age():
    try:
        return time.time() - os.stat(SERVERS_FILE).st_mtime
    except OSError:
        return None


def max_tier():
    a = account()
    try:
        return int(a.get("max_tier", 0))
    except (TypeError, ValueError):
        return 0


class ApiServer(object):
    __slots__ = ("name", "entry_cc", "country", "city", "tier", "features", "load", "score", "status",
                 "phys", "ping")
    source = "api"
    proto = "wireguard"
    valid = True

    def __init__(self, row):
        (self.name, self.entry_cc, self.country, self.city, self.tier, self.features,
         self.load, self.score, self.status, self.phys) = row[:10]
        self.country = (self.country or "").upper()
        if self.country == "GB":
            self.country = "UK"
        self.ping = None

    @property
    def key(self):
        return "api:" + self.name

    @property
    def filename(self):
        return self.key

    @property
    def entry(self):
        return self.entry_cc if self.secure_core else ""

    @property
    def secure_core(self):
        return bool(self.features & F_SECURE_CORE)

    @property
    def tor(self):
        return bool(self.features & F_TOR)

    @property
    def p2p(self):
        return bool(self.features & F_P2P)

    @property
    def streaming(self):
        return bool(self.features & F_STREAMING)

    @property
    def free(self):
        return self.tier == 0

    @property
    def online(self):
        return self.status == 1 and any(p[3] == 1 for p in self.phys)

    @property
    def endpoint_host(self):
        return self.best_phys()[0]

    @property
    def endpoints(self):
        return [(self.best_phys()[0], WG_PORT, "udp")]

    def best_phys(self):
        for p in self.phys:
            if p[3] == 1:
                return p
        return self.phys[0]

    def allowed(self, tier):
        return self.tier <= tier

    def proto_label(self):
        return "WireGuard"

    def title(self, lang="en"):
        name = configs.country_name(self.country, lang)
        if self.secure_core:
            name = "%s > %s" % (configs.country_name(self.entry_cc, lang), name)
        return name

    def short_id(self):
        return self.name

    def tags(self):
        t = []
        if self.free:
            t.append("Free")
        if self.p2p:
            t.append("P2P")
        if self.streaming:
            t.append("Streaming")
        if self.tor:
            t.append("Tor")
        return t

    def __repr__(self):
        return "<ApiServer %s load=%s>" % (self.name, self.load)


_api_cache = {"mtime": None, "servers": []}


def load_servers():
    try:
        mt = os.stat(SERVERS_FILE).st_mtime
    except OSError:
        return []
    if _api_cache["mtime"] == mt:
        return _api_cache["servers"]
    try:
        with open(SERVERS_FILE) as f:
            doc = json.load(f)
        rows = doc.get("s") or []
        servers = [ApiServer(r) for r in rows if len(r) >= 10 and r[9]]
    except (IOError, OSError, ValueError, TypeError):
        servers = []
    _api_cache["mtime"], _api_cache["servers"] = mt, servers
    return servers


class Country(object):
    __slots__ = ("code", "servers", "secure_core")

    def __init__(self, code, secure_core=False):
        self.code, self.servers, self.secure_core = code, [], secure_core

    @property
    def key(self):
        return ("sc:" if self.secure_core else "cc:") + self.code

    def best(self, tier, free_only=False):
        c = [s for s in self.servers if s.online and s.allowed(tier) and not s.tor and (s.free or not free_only)]
        return min(c, key=lambda s: s.score) if c else None

    def min_load(self, tier):
        loads = [s.load for s in self.servers if s.online and s.allowed(tier)]
        return min(loads) if loads else None

    def accessible(self, tier):
        return any(s.allowed(tier) for s in self.servers)

    def cities(self):
        seen = []
        for s in self.servers:
            if s.city and s.city not in seen:
                seen.append(s.city)
        return seen


def countries(servers, secure_core=False, lang="en"):
    by = {}
    for s in servers:
        if s.secure_core != secure_core:
            continue
        by.setdefault(s.country, Country(s.country, secure_core)).servers.append(s)
    out = list(by.values())
    for c in out:
        c.servers.sort(key=lambda s: (not s.online, s.tier == 0 and False, _num(s.name)))
    out.sort(key=lambda c: configs.collate(configs.country_name(c.code, lang), lang))
    return out


def _num(name):
    try:
        return int(name.rsplit("#", 1)[1])
    except (IndexError, ValueError):
        return 0


def best_server(servers, settings, country=None, secure_core=False):
    tier = max_tier()
    free_only = settings["fastest_free_only"]
    c = [s for s in servers if s.online and s.allowed(tier) and not s.tor and s.secure_core == secure_core
         and (country is None or s.country == country) and (s.free or not free_only)]
    if not c and free_only:
        c = [s for s in servers if s.online and s.allowed(tier) and not s.tor and s.secure_core == secure_core
             and (country is None or s.country == country)]
    return min(c, key=lambda s: s.score) if c else None


def cert_args(settings):
    a = ["--netshield=%d" % int(settings["netshield"] or 0)]
    if not settings["vpn_accelerator"]:
        a.append("--no-accelerator")
    if not settings["moderate_nat"]:
        a.append("--strict-nat")
    return a


def ensure_cert(settings):
    r = call(["cert"] + cert_args(settings))
    if r.get("ok"):
        return r["wg_private_key"], None
    return None, r.get("error") or "certificate error"


def wireguard_config(server, private_key):
    phys = server.best_phys()
    return {
        "private_key": private_key,
        "addresses": ["10.2.0.2/32"],
        "dns": ["10.2.0.1"],
        "mtu": 1420,
        "public_key": phys[1],
        "preshared_key": "",
        "allowed_ips": ["0.0.0.0/0"],
        "keepalive": 25,
        "meta": {},
        "domain": phys[2],
        "endpoint": (phys[0], WG_PORT),
    }


OVPN_TEMPLATE = """client
dev tun
proto %(proto)s
%(remotes)s
remote-random
resolv-retry infinite
nobind
cipher AES-256-GCM
ignore-unknown-option data-ciphers
data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305
auth-user-pass
verb 3
tun-mtu 1500
mssfix 0
persist-key
persist-tun
reneg-sec 0
remote-cert-tls server
<ca>
%(ca)s</ca>
<tls-crypt>
-----BEGIN OpenVPN Static key V1-----
6acef03f62675b4b1bbd03e53b187727
423cea742242106cb2916a8a4c829756
3d22c7e5cef430b1103c6f66eb1fc5b3
75a672f158e2e2e936c3faa48b035a6d
e17beaac23b5f03b10b868d53d03521d
8ba115059da777a60cbfd7b2c9c57472
78a15b8f6e68a3ef7fd583ec9f398c8b
d4735dab40cbd1e3c62a822e97489186
c30a0b48c7c38ea32ceb056d3fa5a710
e10ccc7a0ddb363b08c3d2777a3395e1
0c0b6080f56309192ab5aacd4b45f55d
a61fc77af39bd81a19218a79762c3386
2df55785075f37d8c71dc8a42097ee43
344739a0dd48d03025b0450cf1fb5e8c
aeb893d9a96d1f15519bb3c4dcb40ee3
16672ea16c012664f8a9f11255518deb
-----END OpenVPN Static key V1-----
</tls-crypt>
"""


def openvpn_server(server, transport):
    ip = server.best_phys()[0]
    with open(os.path.join(PLUGIN_DIR, "proton_ca.pem")) as f:
        ca = f.read()
    ports = OVPN_PORTS[transport]
    remotes = "\n".join("remote %s %d" % (ip, p) for p in ports)
    d = os.path.dirname(GENERATED_OVPN)
    if not os.path.isdir(d):
        os.makedirs(d)
    with open(GENERATED_OVPN, "w") as f:
        f.write(OVPN_TEMPLATE % {"proto": transport, "remotes": remotes, "ca": ca})
    s = configs.Server(GENERATED_OVPN)
    s.proto, s.transport, s.valid = "openvpn", transport, True
    s.endpoints = [(ip, p, transport) for p in ports]
    s.country, s.entry, s.label = server.country, server.entry, server.name
    return s


def openvpn_credentials(settings):
    a = account()
    user = a.get("ovpn_user") or settings["ovpn_user"]
    pw = a.get("ovpn_pass") or settings["ovpn_pass"]
    ns = int(settings["netshield"] or 0)
    if user and ns and "+f" not in user:
        user += "+f%d" % ns
    return user, pw


def start_agent(domain, settings):
    h = helper()
    if not h or not domain:
        return None
    args = [h, "agent", "--domain=" + domain] + [a for a in cert_args(settings) if a != "--force"]
    try:
        try:
            os.remove(AGENT_FILE)
        except OSError:
            pass
        p = subprocess.Popen(args, stdin=open(os.devnull), stdout=open(os.devnull, "w"),
                             stderr=subprocess.STDOUT, close_fds=True, preexec_fn=os.setsid)
        return p.pid
    except OSError:
        return None


def agent_state():
    try:
        with open(AGENT_FILE) as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return {}


def proton_ping(servers):
    lines, by_ip = [], {}
    for s in servers:
        if getattr(s, "source", "") == "api":
            ip, key = s.best_phys()[0], s.best_phys()[1]
        elif s.proto == "wireguard" and s.wg:
            ip, key = s.endpoint_host, s.wg.get("public_key")
        else:
            continue
        if not ip or not key:
            continue
        by_ip.setdefault(ip, []).append(s)
        lines.append("%s %d %s" % (ip, WG_PORT if getattr(s, "source", "") == "api" else s.endpoints[0][1], key))
    if not lines:
        return []
    r = call(["ping"], stdin="\n".join(lines) + "\n", timeout=30)
    done = []
    for ip, ms in (r.get("ping") or {}).items():
        for s in by_ip.get(ip, []):
            s.ping = ms
            done.append(s)
    return done
