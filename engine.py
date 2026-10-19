# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import base64
import binascii
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time

try:
    from . import api, configs
    from .settings import get_settings
except (ValueError, ImportError, SystemError):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import api
    import configs
    from settings import get_settings

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
RUN_DIR = "/var/run/protonvpn"
STATE_FILE = os.path.join(RUN_DIR, "state.json")
WG_LOG = os.path.join(RUN_DIR, "wireguard.log")
OVPN_LOG = os.path.join(RUN_DIR, "openvpn.log")
OVPN_AUTH = os.path.join(configs.ETC_DIR, "openvpn-auth.txt")
RESOLV = "/etc/resolv.conf"
RESOLV_BAK = os.path.join(configs.ETC_DIR, "resolv.conf.bak")
IPV6_BAK = os.path.join(configs.ETC_DIR, "ipv6.bak")
WG_SOCK_DIR = "/var/run/wireguard"
WG_IF = "proton0"
OVPN_IF = "proton1"
KS_CHAIN = "PROTONVPN_KS"
LAN_NETS4 = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10", "224.0.0.0/4")
LAN_NETS6 = ("fe80::/10", "fc00::/7", "ff00::/8")
HANDSHAKE_STALE = 185

_lock = threading.Lock()


def log(msg):
    try:
        if not os.path.isdir(RUN_DIR):
            os.makedirs(RUN_DIR)
        with open(os.path.join(RUN_DIR, "protonvpn.log"), "a") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except (IOError, OSError):
        pass


def run(cmd, quiet=False):
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, close_fds=True)
        out = p.communicate()[0]
        out = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
        if p.returncode != 0 and not quiet:
            log("cmd failed (%d): %s -> %s" % (p.returncode, " ".join(cmd), out.strip()[:200]))
        return p.returncode, out
    except OSError as e:
        if not quiet:
            log("cmd error: %s -> %s" % (" ".join(cmd), e))
        return 127, str(e)


def which(name):
    for d in os.environ.get("PATH", "/usr/sbin:/usr/bin:/sbin:/bin").split(":") + ["/usr/sbin", "/sbin"]:
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def read_file(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except (IOError, OSError):
        return default


def write_file(path, data, mode=None):
    with open(path, "w") as f:
        f.write(data)
    if mode is not None:
        try:
            os.chmod(path, mode)
        except OSError:
            pass


def pid_alive(pid):
    try:
        pid = int(pid)
        os.kill(pid, 0)
    except (OSError, ValueError, TypeError):
        return False
    stat = read_file("/proc/%d/stat" % pid)
    if stat and stat.rpartition(")")[2].split()[:1] == ["Z"]:
        try:
            os.waitpid(pid, os.WNOHANG)
        except OSError:
            pass
        return False
    return True


def iface_exists(name):
    return os.path.exists("/sys/class/net/%s" % name)


def is_ipv6(addr):
    return ":" in addr


def b64_to_hex(key):
    return binascii.hexlify(base64.b64decode(key.strip())).decode("ascii")


def resolve(host):
    try:
        socket.inet_aton(host)
        return host
    except (socket.error, OSError):
        pass
    try:
        return socket.gethostbyname(host)
    except (socket.error, OSError):
        return None


def default_route():
    for line in read_file("/proc/net/route").splitlines()[1:]:
        f = line.split()
        if len(f) < 8:
            continue
        dev, dest, gw, mask = f[0], f[1], f[2], f[7]
        if dest == "00000000" and mask == "00000000" and not dev.startswith("proton"):
            g = socket.inet_ntoa(binascii.unhexlify(gw)[::-1]) if gw != "00000000" else None
            return g, dev
    return None, None


class Net(object):
    def __init__(self):
        self.ip = which("ip")

    def addr_add(self, dev, cidr):
        if self.ip:
            rc, _ = run([self.ip] + (["-6"] if is_ipv6(cidr) else []) + ["addr", "add", cidr, "dev", dev])
            return rc == 0
        if is_ipv6(cidr):
            return run(["ifconfig", dev, "add", cidr])[0] == 0
        addr, _, plen = cidr.partition("/")
        return run(["ifconfig", dev, addr, "netmask", prefix_to_mask(int(plen or 32))])[0] == 0

    def link_up(self, dev, mtu):
        if self.ip:
            run([self.ip, "link", "set", "dev", dev, "mtu", str(mtu)])
            return run([self.ip, "link", "set", "dev", dev, "up"])[0] == 0
        run(["ifconfig", dev, "mtu", str(mtu)])
        return run(["ifconfig", dev, "up"])[0] == 0

    def link_del(self, dev):
        if not iface_exists(dev):
            return
        if self.ip:
            run([self.ip, "link", "del", "dev", dev], quiet=True)
        else:
            run(["ifconfig", dev, "down"], quiet=True)

    def route_add(self, net, dev=None, via=None):
        v6 = is_ipv6(net)
        if self.ip:
            cmd = [self.ip] + (["-6"] if v6 else []) + ["route", "replace", net]
            if via:
                cmd += ["via", via]
            if dev:
                cmd += ["dev", dev]
            return run(cmd)[0] == 0
        addr, _, plen = net.partition("/")
        if v6:
            cmd = ["route", "-A", "inet6", "add", net]
        elif plen in ("", "32"):
            cmd = ["route", "add", "-host", addr]
        else:
            cmd = ["route", "add", "-net", addr, "netmask", prefix_to_mask(int(plen))]
        if via:
            cmd += ["gw", via]
        if dev:
            cmd += ["dev", dev]
        return run(cmd)[0] == 0

    def route_del(self, net, dev=None):
        v6 = is_ipv6(net)
        if self.ip:
            cmd = [self.ip] + (["-6"] if v6 else []) + ["route", "del", net]
            if dev:
                cmd += ["dev", dev]
            return run(cmd, quiet=True)[0] == 0
        addr, _, plen = net.partition("/")
        if v6:
            return run(["route", "-A", "inet6", "del", net], quiet=True)[0] == 0
        if plen in ("", "32"):
            return run(["route", "del", "-host", addr], quiet=True)[0] == 0
        return run(["route", "del", "-net", addr, "netmask", prefix_to_mask(int(plen))], quiet=True)[0] == 0


def prefix_to_mask(plen):
    m = (0xffffffff << (32 - plen)) & 0xffffffff
    return socket.inet_ntoa(binascii.unhexlify("%08x" % m))


def arch_candidates():
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


def wireguard_command(iface):
    h = api.helper()
    if h:
        return [h, "wg", iface]
    p = os.path.join(PLUGIN_DIR, "bin", "wireguard-go")
    if os.path.isfile(p):
        os.chmod(p, 0o755)
        return [p, iface]
    p = which("wireguard-go")
    return [p, iface] if p else None


def openvpn_binary():
    return which("openvpn")


def install_openvpn():
    if which("opkg"):
        run(["opkg", "update"])
        rc, out = run(["opkg", "install", "openvpn"])
    elif which("apt-get"):
        run(["apt-get", "update"])
        rc, out = run(["apt-get", "install", "-y", "openvpn"])
    else:
        return False, "no package manager"
    return bool(openvpn_binary()), out


def ensure_tun():
    run(["modprobe", "tun"], quiet=True)
    if not os.path.exists("/dev/net/tun"):
        if not os.path.isdir("/dev/net"):
            os.makedirs("/dev/net")
        run(["mknod", "/dev/net/tun", "c", "10", "200"])
        try:
            os.chmod("/dev/net/tun", 0o600)
        except OSError:
            pass
    return os.path.exists("/dev/net/tun")


def uapi(iface, request, timeout=4.0):
    path = os.path.join(WG_SOCK_DIR, iface + ".sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        s.sendall(request.encode("ascii"))
        buf = b""
        while not buf.endswith(b"\n\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    result = {}
    for line in buf.decode("ascii", "replace").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            result.setdefault(k, v)
    return result


def wg_stats(iface=WG_IF):
    try:
        r = uapi(iface, "get=1\n\n", timeout=2.0)
    except (socket.error, OSError):
        return None
    hs = int(r.get("last_handshake_time_sec", "0") or 0)
    return {
        "rx": int(r.get("rx_bytes", "0") or 0),
        "tx": int(r.get("tx_bytes", "0") or 0),
        "handshake": hs,
        "handshake_age": (time.time() - hs) if hs else None,
    }


def dns_apply(servers):
    if not servers:
        return
    if not os.path.exists(RESOLV_BAK):
        write_file(RESOLV_BAK, read_file(RESOLV))
    want = "".join("nameserver %s\n" % s for s in servers)
    if read_file(RESOLV) != want:
        try:
            write_file(RESOLV, want)
        except (IOError, OSError) as e:
            log("resolv.conf write failed: %s" % e)


def dns_current():
    return re.findall(r"^\s*nameserver\s+(\S+)", read_file(RESOLV), re.M)


def dns_restore():
    if os.path.exists(RESOLV_BAK):
        try:
            write_file(RESOLV, read_file(RESOLV_BAK))
            os.remove(RESOLV_BAK)
        except (IOError, OSError) as e:
            log("resolv.conf restore failed: %s" % e)


_V6_KEYS = ("/proc/sys/net/ipv6/conf/all/disable_ipv6", "/proc/sys/net/ipv6/conf/default/disable_ipv6")


def ipv6_disable():
    if not os.path.exists(_V6_KEYS[0]):
        return
    if not os.path.exists(IPV6_BAK):
        write_file(IPV6_BAK, json.dumps(dict((k, read_file(k).strip()) for k in _V6_KEYS)))
    for k in _V6_KEYS:
        try:
            write_file(k, "1")
        except (IOError, OSError):
            pass


def ipv6_restore():
    if not os.path.exists(IPV6_BAK):
        return
    try:
        old = json.loads(read_file(IPV6_BAK) or "{}")
    except ValueError:
        old = {}
    for k, v in old.items():
        try:
            write_file(k, v or "0")
        except (IOError, OSError):
            pass
    try:
        os.remove(IPV6_BAK)
    except OSError:
        pass


def killswitch_supported():
    ipt = which("iptables")
    return bool(ipt) and run([ipt, "-n", "-L", "OUTPUT"], quiet=True)[0] == 0


def killswitch_on(vpn_if, endpoints):
    ok = True
    for tool, lans in (("iptables", LAN_NETS4), ("ip6tables", LAN_NETS6)):
        ipt = which(tool)
        if not ipt:
            continue
        run([ipt, "-N", KS_CHAIN], quiet=True)
        run([ipt, "-F", KS_CHAIN], quiet=True)
        rules = [["-o", "lo", "-j", "RETURN"], ["-o", vpn_if, "-j", "RETURN"]]
        for n in lans:
            rules.append(["-d", n, "-j", "RETURN"])
        for host, port, proto in endpoints:
            if is_ipv6(host) == (tool == "ip6tables"):
                rules.append(["-d", host, "-p", proto, "--dport", str(port), "-j", "RETURN"])
        if tool == "iptables":
            rules.append(["-p", "udp", "--dport", "67:68", "-j", "RETURN"])
        rules.append(["-j", "REJECT"])
        for r in rules:
            if run([ipt, "-A", KS_CHAIN] + r)[0] != 0 and r[-1] == "REJECT":
                run([ipt, "-A", KS_CHAIN, "-j", "DROP"])
        while run([ipt, "-D", "OUTPUT", "-j", KS_CHAIN], quiet=True)[0] == 0:
            pass
        if run([ipt, "-I", "OUTPUT", "1", "-j", KS_CHAIN])[0] != 0:
            ok = ok and tool != "iptables"
    return ok


def killswitch_off():
    for tool in ("iptables", "ip6tables"):
        ipt = which(tool)
        if not ipt:
            continue
        while run([ipt, "-D", "OUTPUT", "-j", KS_CHAIN], quiet=True)[0] == 0:
            pass
        run([ipt, "-F", KS_CHAIN], quiet=True)
        run([ipt, "-X", KS_CHAIN], quiet=True)


def killswitch_active():
    ipt = which("iptables")
    return bool(ipt) and run([ipt, "-n", "-L", KS_CHAIN], quiet=True)[0] == 0


def load_state():
    try:
        return json.loads(read_file(STATE_FILE) or "null")
    except ValueError:
        return None


def save_state(st):
    if not os.path.isdir(RUN_DIR):
        os.makedirs(RUN_DIR)
    write_file(STATE_FILE, json.dumps(st))


def clear_state():
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


def _kill(pid, wait=3.0):
    if not pid_alive(pid):
        return
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError:
        return
    t = time.time() + wait
    while time.time() < t and pid_alive(pid):
        time.sleep(0.1)
    if pid_alive(pid):
        try:
            os.kill(int(pid), signal.SIGKILL)
        except OSError:
            pass


def _spawn(cmd, logfile, env=None):
    e = dict(os.environ)
    e.update(env or {})
    lf = open(logfile, "a")
    try:
        p = subprocess.Popen(cmd, stdin=open(os.devnull), stdout=lf, stderr=subprocess.STDOUT,
                             close_fds=True, env=e, preexec_fn=os.setsid)
    finally:
        lf.close()
    return p


class ConnectError(Exception):
    pass


class Engine(object):
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.net = Net()

    def connect(self, server, progress=None):
        progress = progress or (lambda m: None)
        with _lock:
            target, creds, domain = server, None, None
            if getattr(server, "source", "") == "api":
                proto = self.settings["protocol"]
                if proto == "wireguard":
                    progress("cert")
                    pk, err = api.ensure_cert(self.settings)
                    if not pk:
                        return False, "CERT: %s" % err
                    target = configs.Server(server.key + ".conf")
                    target.proto, target.valid = "wireguard", True
                    target.wg = api.wireguard_config(server, pk)
                    target.endpoints = [(target.wg["endpoint"][0], target.wg["endpoint"][1], "udp")]
                    domain = target.wg["domain"]
                else:
                    target = api.openvpn_server(server, "udp" if proto == "openvpn-udp" else "tcp")
                    creds = api.openvpn_credentials(self.settings)
                target.country, target.entry, target.label = server.country, server.entry, server.short_id()
            self._teardown(keep_killswitch=True)
            try:
                if target.proto == "wireguard":
                    st = self._connect_wireguard(target, progress)
                else:
                    st = self._connect_openvpn(target, progress, creds)
                st["server"], st["label"] = server.key, server.short_id()
                if domain:
                    st["domain"] = domain
                    st["agent_pid"] = api.start_agent(domain, self.settings)
            except ConnectError as e:
                log("connect failed: %s" % e)
                self._teardown(keep_killswitch=True)
                return False, str(e)
            except Exception as e:
                log("connect crashed: %r" % e)
                self._teardown(keep_killswitch=True)
                return False, "error: %s" % e
            save_state(st)
            self.settings["last_server"] = server.key
            try:
                self.settings.save()
            except (IOError, OSError):
                pass
            log("connected %s via %s" % (server.short_id(), server.proto))
            return True, server.short_id()

    def disconnect(self):
        with _lock:
            self._teardown()
        log("disconnected")
        return True

    def status(self):
        st = load_state()
        if not st:
            return {"connected": False, "killswitch": killswitch_active()}
        alive = pid_alive(st.get("pid")) and iface_exists(st.get("iface", ""))
        res = dict(st)
        res["connected"] = alive
        res["killswitch"] = killswitch_active()
        res["uptime"] = int(time.time() - st.get("since", time.time()))
        if st.get("proto") == "wireguard" and alive:
            s = wg_stats(st["iface"]) or {}
            res.update(s)
        elif alive:
            rx = read_file("/sys/class/net/%s/statistics/rx_bytes" % st["iface"]).strip()
            tx = read_file("/sys/class/net/%s/statistics/tx_bytes" % st["iface"]).strip()
            res["rx"] = int(rx or 0)
            res["tx"] = int(tx or 0)
        return res

    def health(self):
        st = load_state()
        if not st:
            return None
        if not pid_alive(st.get("pid")):
            return "process died"
        if not iface_exists(st.get("iface", "")):
            return "interface gone"
        if st.get("proto") == "wireguard":
            s = wg_stats(st["iface"])
            if s is None:
                return "uapi unreachable"
            age = s["handshake_age"]
            if age is None or age > HANDSHAKE_STALE:
                return "handshake stale"
        if st.get("dns") and self.settings["use_vpn_dns"] and dns_current() != st["dns"]:
            dns_apply(st["dns"])
        return "ok"

    def _prepare_common(self, progress):
        progress("tun")
        if not ensure_tun():
            raise ConnectError("TUN device not available (kernel module 'tun' missing)")
        gw, dev = default_route()
        if not dev:
            raise ConnectError("no internet connection (no default route)")
        return gw, dev

    def _connect_wireguard(self, server, progress):
        wg = server.wg
        wg_cmd = wireguard_command(WG_IF)
        if not wg_cmd:
            raise ConnectError("WireGuard engine missing for this CPU")
        gw, dev = self._prepare_common(progress)
        host, port, _ = server.endpoints[0]
        ip = resolve(host)
        if not ip:
            raise ConnectError("cannot resolve %s" % host)

        if killswitch_active():
            killswitch_on(WG_IF, [(ip, port, "udp")])
        progress("start")
        if not os.path.isdir(RUN_DIR):
            os.makedirs(RUN_DIR)
        write_file(WG_LOG, "")
        p = _spawn(wg_cmd, WG_LOG, {"WG_PROCESS_FOREGROUND": "1", "LOG_LEVEL": "error"})
        sock = os.path.join(WG_SOCK_DIR, WG_IF + ".sock")
        t = time.time() + 8
        while time.time() < t and not os.path.exists(sock):
            if p.poll() is not None:
                raise ConnectError("wireguard-go exited: %s" % read_file(WG_LOG).strip()[-200:])
            time.sleep(0.1)
        if not os.path.exists(sock):
            _kill(p.pid)
            raise ConnectError("wireguard-go did not start")
        st = {"proto": "wireguard", "iface": WG_IF, "pid": p.pid, "server": server.key,
              "label": server.short_id(), "country": server.country, "entry": server.entry,
              "endpoint": ip, "endpoint_port": port, "gw": gw, "dev": dev, "since": time.time(),
              "routes": [], "dns": [], "v6": False}
        save_state(st)

        progress("configure")
        req = "set=1\nprivate_key=%s\nreplace_peers=true\npublic_key=%s\n" % (
            b64_to_hex(wg["private_key"]), b64_to_hex(wg["public_key"]))
        if wg.get("preshared_key"):
            req += "preshared_key=%s\n" % b64_to_hex(wg["preshared_key"])
        req += "endpoint=%s:%d\npersistent_keepalive_interval=%d\nreplace_allowed_ips=true\n" % (
            ip, port, wg.get("keepalive") or 25)
        for a in wg["allowed_ips"]:
            req += "allowed_ip=%s\n" % a
        r = uapi(WG_IF, req + "\n")
        if r.get("errno", "0") != "0":
            raise ConnectError("WireGuard rejected the config (errno %s)" % r.get("errno"))

        v6_addr = [a for a in wg["addresses"] if is_ipv6(a)]
        for a in wg["addresses"]:
            if not is_ipv6(a) and not self.net.addr_add(WG_IF, a):
                raise ConnectError("cannot set address %s" % a)
        if not self.net.link_up(WG_IF, wg.get("mtu") or 1420):
            raise ConnectError("cannot bring %s up" % WG_IF)
        v6_ok = False
        if v6_addr and os.path.exists(_V6_KEYS[0]) and "::/0" in wg["allowed_ips"]:
            v6_ok = all(self.net.addr_add(WG_IF, a) for a in v6_addr)

        progress("route")
        self.net.route_add("%s/32" % ip, dev=dev, via=gw)
        st["routes"].append("%s/32" % ip)
        full = "0.0.0.0/0" in wg["allowed_ips"]
        nets = ["0.0.0.0/1", "128.0.0.0/1"] if full else [a for a in wg["allowed_ips"] if not is_ipv6(a)]
        if v6_ok:
            nets += ["::/1", "8000::/1"]
        for n in nets:
            if not self.net.route_add(n, dev=WG_IF):
                raise ConnectError("cannot add route %s" % n)
        st["v6"] = v6_ok
        if full and not v6_ok and self.settings["block_ipv6"]:
            ipv6_disable()
        save_state(st)

        progress("handshake")
        t = time.time() + 12
        ok = False
        while time.time() < t:
            s = wg_stats(WG_IF)
            if s and s["handshake"]:
                ok = True
                break
            if p.poll() is not None:
                break
            time.sleep(0.3)
        if not ok:
            raise ConnectError("no handshake with %s - server offline or config revoked" % host)

        if self.settings["use_vpn_dns"] and wg.get("dns"):
            st["dns"] = [d for d in wg["dns"] if v6_ok or not is_ipv6(d)]
            dns_apply(st["dns"])
        if self.settings["killswitch"]:
            st["killswitch"] = killswitch_on(WG_IF, [(ip, port, "udp")])
        return st

    def _connect_openvpn(self, server, progress, creds=None):
        binary = openvpn_binary()
        if not binary:
            raise ConnectError("OPENVPN_MISSING")
        user, pw = creds or (self.settings["ovpn_user"], self.settings["ovpn_pass"])
        if not user or not pw:
            raise ConnectError("OPENVPN_CREDENTIALS")
        gw, dev = self._prepare_common(progress)
        write_file(OVPN_AUTH, "%s\n%s\n" % (user, pw), 0o600)
        script = os.path.join(PLUGIN_DIR, "scripts", "updown.sh")
        if os.path.isfile(script):
            os.chmod(script, 0o755)
        write_file(OVPN_LOG, "")
        endpoints = []
        for h, p, t in server.endpoints:
            ip = resolve(h)
            if ip:
                endpoints.append((ip, p, t))
        if not endpoints:
            raise ConnectError("cannot resolve VPN server")
        if killswitch_active():
            killswitch_on(OVPN_IF, endpoints)

        if self.settings["use_vpn_dns"] and not os.path.exists(RESOLV_BAK):
            write_file(RESOLV_BAK, read_file(RESOLV))
        cmd = [binary, "--config", server.path,
               "--auth-user-pass", OVPN_AUTH, "--auth-nocache",
               "--dev", OVPN_IF, "--dev-type", "tun",
               "--script-security", "2", "--up", script, "--down", script,
               "--setenv", "PVPN_DNS", "1" if self.settings["use_vpn_dns"] else "0",
               "--connect-retry-max", "3", "--verb", "3", "--log", OVPN_LOG]
        if self.settings["block_ipv6"]:
            cmd += ["--pull-filter", "ignore", "ifconfig-ipv6", "--pull-filter", "ignore", "route-ipv6"]
        progress("start")
        p = _spawn(cmd, os.devnull)
        st = {"proto": "openvpn", "iface": OVPN_IF, "pid": p.pid, "server": server.key,
              "label": server.short_id(), "country": server.country, "entry": server.entry,
              "endpoint": endpoints[0][0], "endpoint_port": endpoints[0][1], "gw": gw, "dev": dev,
              "since": time.time(), "routes": [], "dns": [], "v6": False}
        save_state(st)
        progress("handshake")
        t = time.time() + 45
        while time.time() < t:
            txt = read_file(OVPN_LOG)
            if "Initialization Sequence Completed" in txt:
                break
            if "AUTH_FAILED" in txt:
                raise ConnectError("OPENVPN_AUTH_FAILED")
            if p.poll() is not None:
                last = [l for l in txt.splitlines() if l.strip()][-1:] or ["?"]
                raise ConnectError("openvpn exited: %s" % last[0][-160:])
            time.sleep(0.5)
        else:
            raise ConnectError("OpenVPN connection timed out")
        if self.settings["block_ipv6"]:
            ipv6_disable()
        if self.settings["use_vpn_dns"]:
            st["dns"] = dns_current()
        if self.settings["killswitch"]:
            st["killswitch"] = killswitch_on(OVPN_IF, endpoints)
        return st

    def _teardown(self, keep_killswitch=False):
        st = load_state()
        if st:
            _kill(st.get("agent_pid"))
            _kill(st.get("pid"))
            for r in st.get("routes", []):
                self.net.route_del(r)
            self.net.link_del(st.get("iface", ""))
        for ifn in (WG_IF, OVPN_IF):
            self.net.link_del(ifn)
        try:
            os.remove(os.path.join(WG_SOCK_DIR, WG_IF + ".sock"))
        except OSError:
            pass
        dns_restore()
        ipv6_restore()
        if not keep_killswitch:
            killswitch_off()
        clear_state()


def ping(host, timeout=1):
    pb = which("ping")
    if pb:
        rc, out = run([pb, "-c", "1", "-W", str(timeout), host], quiet=True)
        m = re.search(r"time[=<]\s*([\d.]+)\s*ms", out)
        if rc == 0 and m:
            return int(float(m.group(1)) + 0.5)
        if rc == 0:
            return 1
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout + 0.5)
    t = time.time()
    try:
        s.connect((host, 443))
        return int((time.time() - t) * 1000 + 0.5)
    except (socket.error, OSError):
        return -1
    finally:
        s.close()


def ping_servers(servers, workers=12, callback=None):
    done = set(id(x) for x in api.proton_ping(servers))
    rest = [s for s in servers if id(s) not in done]
    if not rest:
        return servers
    _ping_classic(rest, workers, callback)
    return servers


def _ping_classic(servers, workers=12, callback=None):
    hosts = {}
    for s in servers:
        hosts.setdefault(s.endpoint_host, []).append(s)
    queue = list(hosts.keys())
    qlock = threading.Lock()

    def worker():
        while True:
            with qlock:
                if not queue:
                    return
                h = queue.pop()
            ip = resolve(h)
            ms = ping(ip) if ip else -1
            for s in hosts[h]:
                s.ping = ms
            if callback:
                callback()

    threads = [threading.Thread(target=worker) for _ in range(min(workers, len(queue)) or 1)]
    for t in threads:
        t.daemon = True
        t.start()
    for t in threads:
        t.join()
    return servers


def pick_fastest(servers, settings):
    live = [s for s in servers if getattr(s, "source", "") == "api"]
    if live:
        return api.best_server(live, settings)
    pref = settings["fastest_proto"]
    cands = [s for s in servers if pref == "any" or s.proto == pref] or list(servers)
    if settings["fastest_free_only"]:
        cands = [s for s in cands if s.free] or cands
    if not cands:
        return None
    ping_servers(cands)
    good = [s for s in cands if s.ping is not None and s.ping >= 0]
    return min(good, key=lambda s: s.ping) if good else None


def public_ip_info(timeout=6):
    try:
        from urllib.request import urlopen
    except ImportError:
        from urllib2 import urlopen
    urls = ("http://ip-api.com/json/?fields=status,country,countryCode,city,isp,org,query",
            "https://ipinfo.io/json")
    for u in urls:
        try:
            data = json.loads(urlopen(u, timeout=timeout).read().decode("utf-8"))
        except Exception:
            continue
        if "query" in data:
            return {"ip": data.get("query"), "country": data.get("country", ""),
                    "countryCode": data.get("countryCode", ""), "city": data.get("city", ""),
                    "isp": data.get("org") or data.get("isp", "")}
        if "ip" in data:
            return {"ip": data["ip"], "country": data.get("country", ""), "countryCode": data.get("country", ""),
                    "city": data.get("city", ""), "isp": data.get("org", "")}
    return None


def all_servers():
    live = api.load_servers() if api.logged_in() else []
    return list(live) + configs.load_servers()


def find_server(key):
    if key.startswith("api:"):
        for s in api.load_servers():
            if s.key == key:
                return s
        return None
    for s in configs.load_servers():
        if s.key == key:
            return s
    return None


def maintain(settings=None):
    settings = settings or get_settings()
    st = load_state()
    if not st or not st.get("domain") or not api.logged_in():
        return
    try:
        with open(api.CERT_FILE) as f:
            cert = json.load(f)
    except (IOError, OSError, ValueError):
        cert = {}
    if time.time() < cert.get("RefreshTime", 0):
        return
    pk, err = api.ensure_cert(settings)
    if not pk:
        log("cert renew failed: %s" % err)
        return
    _kill(st.get("agent_pid"))
    st["agent_pid"] = api.start_agent(st["domain"], settings)
    save_state(st)
    log("certificate renewed")


def wait_for_network(timeout=90):
    t = time.time() + timeout
    while time.time() < t:
        if default_route()[1]:
            return True
        time.sleep(2)
    return False


def autoconnect(settings=None):
    settings = settings or get_settings()
    mode = settings["autoconnect"]
    if mode == "off":
        return False, "off"
    if not wait_for_network():
        return False, "no network"
    servers = all_servers()
    if not servers:
        return False, "no servers"
    by_key = dict((s.key, s) for s in servers)
    if mode == "last" and settings["last_server"] and settings["last_server"] not in by_key:
        t = find_server(settings["last_server"])
        if t:
            by_key[t.key] = t
    target = None
    if mode == "last":
        target = by_key.get(settings["last_server"])
    elif mode == "favorite":
        favs = [by_key[k] for k in settings["favorites"] if k in by_key]
        target = pick_fastest(favs, settings) if len(favs) > 1 else (favs[0] if favs else None)
    if target is None:
        target = pick_fastest(servers, settings)
    if target is None:
        return False, "no reachable server"
    return Engine(settings).connect(target)


def _human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.0f %s" % (n, u) if u == "B" else "%.1f %s" % (n, u)
        n /= 1024.0
    return "%.1f TB" % n


def main(argv):
    if len(argv) < 2:
        print("usage: engine.py status|list|connect <file|NAME#N|fastest>|disconnect|import|autostart")
        return 2
    cmd = argv[1]
    eng = Engine()
    if cmd == "status":
        s = eng.status()
        if s.get("connected"):
            print("connected %s (%s) up %ss rx %s tx %s" % (s["label"], s["proto"], s["uptime"],
                                                          _human(s.get("rx", 0)), _human(s.get("tx", 0))))
        else:
            print("disconnected")
        print("killswitch: %s" % ("on" if s.get("killswitch") else "off"))
        return 0
    if cmd == "list":
        for s in all_servers():
            print("%-28s %-22s %-14s %s" % (s.filename, s.short_id(), s.proto_label(), s.endpoint_host))
        return 0
    if cmd == "import":
        print("\n".join(configs.import_all()) or "nothing found")
        return 0
    if cmd == "disconnect":
        eng.disconnect()
        print("disconnected")
        return 0
    if cmd == "autostart":
        ok, msg = autoconnect()
        print(msg)
        return 0 if ok else 1
    if cmd == "connect" and len(argv) > 2:
        servers = all_servers()
        if argv[2] == "fastest":
            target = pick_fastest(servers, eng.settings)
        else:
            target = ([s for s in servers if s.filename == argv[2] or s.short_id() == argv[2]] or [None])[0]
        if not target:
            print("server not found")
            return 1
        ok, msg = eng.connect(target, progress=lambda m: print("..", m))
        print(("connected " if ok else "failed: ") + msg)
        return 0 if ok else 1
    print("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
