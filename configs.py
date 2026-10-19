# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import os
import re
import shutil
import zipfile

ETC_DIR = "/etc/protonvpn"
CONF_DIR = os.path.join(ETC_DIR, "configs")

SECURE_CORE_ENTRIES = ("CH", "IS", "SE")

COUNTRIES = {
    "AD": ("Andorra", "Andorra"), "AE": ("United Arab Emirates", "Birleşik Arap Emirlikleri"),
    "AF": ("Afghanistan", "Afganistan"), "AL": ("Albania", "Arnavutluk"), "AM": ("Armenia", "Ermenistan"),
    "AO": ("Angola", "Angola"), "AR": ("Argentina", "Arjantin"), "AT": ("Austria", "Avusturya"),
    "AU": ("Australia", "Avustralya"), "AZ": ("Azerbaijan", "Azerbaycan"),
    "BA": ("Bosnia and Herzegovina", "Bosna-Hersek"), "BD": ("Bangladesh", "Bangladeş"),
    "BE": ("Belgium", "Belçika"), "BG": ("Bulgaria", "Bulgaristan"), "BH": ("Bahrain", "Bahreyn"),
    "BN": ("Brunei", "Brunei"), "BO": ("Bolivia", "Bolivya"), "BR": ("Brazil", "Brezilya"),
    "BT": ("Bhutan", "Butan"), "BY": ("Belarus", "Belarus"), "BZ": ("Belize", "Belize"),
    "CA": ("Canada", "Kanada"), "CH": ("Switzerland", "İsviçre"), "CI": ("Côte d'Ivoire", "Fildişi Sahili"),
    "CL": ("Chile", "Şili"), "CM": ("Cameroon", "Kamerun"), "CO": ("Colombia", "Kolombiya"),
    "CR": ("Costa Rica", "Kosta Rika"), "CY": ("Cyprus", "Kıbrıs"), "CZ": ("Czechia", "Çekya"),
    "DE": ("Germany", "Almanya"), "DK": ("Denmark", "Danimarka"), "DZ": ("Algeria", "Cezayir"),
    "EC": ("Ecuador", "Ekvador"), "EE": ("Estonia", "Estonya"), "EG": ("Egypt", "Mısır"),
    "ES": ("Spain", "İspanya"), "ET": ("Ethiopia", "Etiyopya"), "FI": ("Finland", "Finlandiya"),
    "FR": ("France", "Fransa"), "GE": ("Georgia", "Gürcistan"), "GH": ("Ghana", "Gana"),
    "GR": ("Greece", "Yunanistan"), "GT": ("Guatemala", "Guatemala"), "HK": ("Hong Kong", "Hong Kong"),
    "HR": ("Croatia", "Hırvatistan"), "HU": ("Hungary", "Macaristan"), "ID": ("Indonesia", "Endonezya"),
    "IE": ("Ireland", "İrlanda"), "IL": ("Israel", "İsrail"), "IN": ("India", "Hindistan"),
    "IQ": ("Iraq", "Irak"), "IS": ("Iceland", "İzlanda"), "IT": ("Italy", "İtalya"),
    "JO": ("Jordan", "Ürdün"), "JP": ("Japan", "Japonya"), "KE": ("Kenya", "Kenya"),
    "KH": ("Cambodia", "Kamboçya"), "KR": ("South Korea", "Güney Kore"), "KW": ("Kuwait", "Kuveyt"),
    "KZ": ("Kazakhstan", "Kazakistan"), "LA": ("Laos", "Laos"), "LB": ("Lebanon", "Lübnan"),
    "LK": ("Sri Lanka", "Sri Lanka"), "LT": ("Lithuania", "Litvanya"), "LU": ("Luxembourg", "Lüksemburg"),
    "LV": ("Latvia", "Letonya"), "LY": ("Libya", "Libya"), "MA": ("Morocco", "Fas"),
    "MD": ("Moldova", "Moldova"), "ME": ("Montenegro", "Karadağ"), "MK": ("North Macedonia", "Kuzey Makedonya"),
    "MM": ("Myanmar", "Myanmar"), "MN": ("Mongolia", "Moğolistan"), "MT": ("Malta", "Malta"),
    "MU": ("Mauritius", "Mauritius"), "MX": ("Mexico", "Meksika"), "MY": ("Malaysia", "Malezya"),
    "MZ": ("Mozambique", "Mozambik"), "NG": ("Nigeria", "Nijerya"), "NL": ("Netherlands", "Hollanda"),
    "NO": ("Norway", "Norveç"), "NP": ("Nepal", "Nepal"), "NZ": ("New Zealand", "Yeni Zelanda"),
    "OM": ("Oman", "Umman"), "PA": ("Panama", "Panama"), "PE": ("Peru", "Peru"),
    "PH": ("Philippines", "Filipinler"), "PK": ("Pakistan", "Pakistan"), "PL": ("Poland", "Polonya"),
    "PR": ("Puerto Rico", "Porto Riko"), "PT": ("Portugal", "Portekiz"), "PY": ("Paraguay", "Paraguay"),
    "QA": ("Qatar", "Katar"), "RO": ("Romania", "Romanya"), "RS": ("Serbia", "Sırbistan"),
    "RU": ("Russia", "Rusya"), "RW": ("Rwanda", "Ruanda"), "SA": ("Saudi Arabia", "Suudi Arabistan"),
    "SD": ("Sudan", "Sudan"), "SE": ("Sweden", "İsveç"), "SG": ("Singapore", "Singapur"),
    "SI": ("Slovenia", "Slovenya"), "SK": ("Slovakia", "Slovakya"), "SN": ("Senegal", "Senegal"),
    "SO": ("Somalia", "Somali"), "SS": ("South Sudan", "Güney Sudan"), "SV": ("El Salvador", "El Salvador"),
    "SY": ("Syria", "Suriye"), "TD": ("Chad", "Çad"), "TG": ("Togo", "Togo"), "TH": ("Thailand", "Tayland"),
    "TJ": ("Tajikistan", "Tacikistan"), "TM": ("Turkmenistan", "Türkmenistan"), "TN": ("Tunisia", "Tunus"),
    "TR": ("Türkiye", "Türkiye"), "TW": ("Taiwan", "Tayvan"), "TZ": ("Tanzania", "Tanzanya"),
    "UA": ("Ukraine", "Ukrayna"), "UG": ("Uganda", "Uganda"), "UK": ("United Kingdom", "Birleşik Krallık"),
    "GB": ("United Kingdom", "Birleşik Krallık"), "US": ("United States", "ABD"),
    "UY": ("Uruguay", "Uruguay"), "UZ": ("Uzbekistan", "Özbekistan"), "VE": ("Venezuela", "Venezuela"),
    "VN": ("Vietnam", "Vietnam"), "YE": ("Yemen", "Yemen"), "ZA": ("South Africa", "Güney Afrika"),
    "ZM": ("Zambia", "Zambiya"), "ZW": ("Zimbabwe", "Zimbabve"),
}


_TR_ORDER = u"0123456789aâbcçdefgğhıiîjklmnoöprsştuüûvwxyz"
_TR_RANK = dict((ch, i) for i, ch in enumerate(_TR_ORDER))


def collate(text, lang="en"):
    if lang != "tr":
        return text.lower()
    t = text.replace(u"I", u"ı").replace(u"İ", u"i").lower()
    return [_TR_RANK.get(ch, 100 + ord(ch)) for ch in t]


def country_name(code, lang="en"):
    names = COUNTRIES.get((code or "").upper())
    if not names:
        return code or "?"
    return names[1] if lang == "tr" else names[0]


class Server(object):

    def __init__(self, path):
        self.path = path
        self.filename = os.path.basename(path)
        self.proto = "wireguard" if path.lower().endswith(".conf") else "openvpn"
        self.transport = "udp"
        self.country = ""
        self.entry = ""
        self.region = ""
        self.number = 0
        self.free = False
        self.tor = False
        self.label = ""
        self.endpoints = []
        self.wg = {}
        self.ping = None
        self.valid = False
        self.error = ""

    @property
    def key(self):
        return self.filename

    @property
    def secure_core(self):
        return bool(self.entry)

    @property
    def endpoint_host(self):
        return self.endpoints[0][0] if self.endpoints else ""

    def proto_label(self):
        if self.proto == "wireguard":
            return "WireGuard"
        return "OpenVPN " + self.transport.upper()

    def title(self, lang="en"):
        name = country_name(self.country, lang)
        if self.entry:
            name = "%s > %s" % (country_name(self.entry, lang), name)
        return name

    def short_id(self):
        if self.label:
            return self.label
        return os.path.splitext(self.filename)[0]

    def __repr__(self):
        return "<Server %s %s %s>" % (self.short_id(), self.proto, self.endpoint_host)


_LABEL_RE = re.compile(r"^([A-Z]{2})(?:-([A-Z]{2,4}))?(?:-([A-Z]{2,4}))?#(\d+)$")
_OVPN_NAME_RE = re.compile(r"^([a-z]{2})(?:-([a-z]{2,4}))?(?:-([a-z]{2,4}))?-(\d+)\.protonvpn\.(udp|tcp)\.ovpn$")
_LOOSE_RE = re.compile(r"^(?:wg-|protonvpn-)?([A-Za-z]{2})(?:[-_ ]([A-Za-z]{2,4}))?(?:[-_ ]([A-Za-z]{2,4}))?[-_ #]*(\d+)?", re.I)


def _apply_label(srv, cc, p2, p3, num):
    cc = (cc or "").upper()
    parts = [p.upper() for p in (p2, p3) if p]
    srv.country = cc
    for p in parts:
        if p == "FREE":
            srv.free = True
        elif p == "TOR":
            srv.tor = True
        elif cc in SECURE_CORE_ENTRIES and len(p) == 2 and p in COUNTRIES and not srv.entry:
            srv.entry, srv.country = cc, p
        elif not srv.region:
            srv.region = p
    try:
        srv.number = int(num or 0)
    except ValueError:
        srv.number = 0
    if cc:
        txt = cc
        for p in parts:
            txt += "-" + p
        srv.label = "%s#%d" % (txt, srv.number) if srv.number else txt


def _parse_wireguard(srv, text):
    section = None
    iface, peer = {}, {}
    peer_comment = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low == "[interface]":
            section = iface
            continue
        if low == "[peer]":
            section = peer
            continue
        if line.startswith("#"):
            c = line.lstrip("#").strip()
            if section is peer and _LABEL_RE.match(c.upper().replace(" ", "")):
                peer_comment = c.upper().replace(" ", "")
            elif section is iface and "=" in c:
                k, v = c.split("=", 1)
                iface.setdefault("_meta", {})[k.strip()] = v.strip()
            continue
        if section is None or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().lower()
        v = v.split("#", 1)[0].strip() if k != "privatekey" and k != "publickey" else v.strip()
        if k in section and k in ("address", "dns", "allowedips"):
            section[k] += "," + v
        else:
            section[k] = v

    def split_list(s):
        return [x.strip() for x in (s or "").split(",") if x.strip()]

    if not iface.get("privatekey") or not peer.get("publickey") or not peer.get("endpoint"):
        srv.error = "missing PrivateKey/PublicKey/Endpoint"
        return
    ep = peer["endpoint"]
    if ep.startswith("["):
        host, _, port = ep[1:].partition("]:")
    else:
        host, _, port = ep.rpartition(":")
    try:
        port = int(port)
    except ValueError:
        srv.error = "bad Endpoint"
        return
    srv.endpoints = [(host, port, "udp")]
    try:
        mtu = int(iface.get("mtu", "0") or 0)
    except ValueError:
        mtu = 0
    try:
        keepalive = int(peer.get("persistentkeepalive", "25") or 25)
    except ValueError:
        keepalive = 25
    srv.wg = {
        "private_key": iface["privatekey"],
        "addresses": split_list(iface.get("address")),
        "dns": split_list(iface.get("dns")),
        "mtu": mtu or 1420,
        "public_key": peer["publickey"],
        "preshared_key": peer.get("presharedkey", ""),
        "allowed_ips": split_list(peer.get("allowedips")) or ["0.0.0.0/0"],
        "keepalive": keepalive,
        "meta": iface.get("_meta", {}),
    }
    if peer_comment:
        m = _LABEL_RE.match(peer_comment)
        _apply_label(srv, *m.groups())
    srv.valid = True


def _parse_openvpn(srv, text):
    proto = "udp"
    remotes = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        parts = line.split()
        if parts[0] == "proto" and len(parts) > 1:
            proto = "tcp" if parts[1].startswith("tcp") else "udp"
        elif parts[0] == "remote" and len(parts) > 1:
            port = 1194
            if len(parts) > 2:
                try:
                    port = int(parts[2])
                except ValueError:
                    pass
            p = parts[3] if len(parts) > 3 else None
            remotes.append((parts[1], port, p))
    if not remotes:
        srv.error = "no 'remote' line"
        return
    srv.transport = proto
    srv.endpoints = [(h, p, ("tcp" if (t or proto).startswith("tcp") else "udp")) for h, p, t in remotes]
    srv.valid = True


def parse_file(path):
    srv = Server(path)
    try:
        with open(path, "rb") as f:
            data = f.read(256 * 1024)
        text = data.decode("utf-8", "replace")
    except (IOError, OSError) as e:
        srv.error = str(e)
        return srv
    if srv.proto == "wireguard":
        _parse_wireguard(srv, text)
    else:
        _parse_openvpn(srv, text)
    if not srv.country:
        name = srv.filename.lower()
        m = _OVPN_NAME_RE.match(name)
        if m:
            _apply_label(srv, m.group(1), m.group(2), m.group(3), m.group(4))
            srv.transport = m.group(5)
        else:
            m = _LOOSE_RE.match(os.path.splitext(srv.filename)[0])
            if m and m.group(1).upper() in COUNTRIES:
                _apply_label(srv, m.group(1), m.group(2), m.group(3), m.group(4))
    return srv


def ensure_dirs():
    for d in (ETC_DIR, CONF_DIR):
        if not os.path.isdir(d):
            os.makedirs(d)
    try:
        os.chmod(CONF_DIR, 0o700)
    except OSError:
        pass


def load_servers(conf_dir=CONF_DIR):
    servers = []
    if not os.path.isdir(conf_dir):
        return servers
    for name in sorted(os.listdir(conf_dir)):
        low = name.lower()
        if low.endswith(".conf") or low.endswith(".ovpn"):
            s = parse_file(os.path.join(conf_dir, name))
            if s.valid:
                servers.append(s)
    return servers


def sort_servers(servers, mode="country", favorites=(), lang="en"):
    favs = set(favorites)

    def k_country(s):
        return (s.key not in favs, collate(s.title(lang), lang), s.region, s.number, s.proto)

    def k_ping(s):
        p = s.ping if (s.ping is not None and s.ping >= 0) else 99999
        return (s.key not in favs, p, collate(s.title(lang), lang))

    return sorted(servers, key=k_ping if mode == "ping" else k_country)


def _valid_payload(name, data):
    text = data[:65536].decode("utf-8", "replace")
    if name.lower().endswith(".conf"):
        return "[Interface]" in text and "PrivateKey" in text and "[Peer]" in text
    return "remote " in text and ("<ca>" in text or "ca " in text)


def _safe_name(name):
    name = os.path.basename(name.replace("\\", "/")).strip()
    name = re.sub(r"[^A-Za-z0-9._#+ -]", "_", name)
    return name or "server.conf"


def store_config(name, data, conf_dir=CONF_DIR):
    name = _safe_name(name)
    low = name.lower()
    if not (low.endswith(".conf") or low.endswith(".ovpn")):
        return None
    if not _valid_payload(name, data):
        return None
    ensure_dirs() if conf_dir == CONF_DIR else None
    if not os.path.isdir(conf_dir):
        os.makedirs(conf_dir)
    dst = os.path.join(conf_dir, name)
    with open(dst, "wb") as f:
        f.write(data)
    try:
        os.chmod(dst, 0o600)
    except OSError:
        pass
    return name


def import_path(path, conf_dir=CONF_DIR):
    stored = []
    low = path.lower()
    try:
        if low.endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                for info in z.infolist():
                    if info.file_size > 256 * 1024:
                        continue
                    n = info.filename.lower()
                    if n.endswith(".conf") or n.endswith(".ovpn"):
                        r = store_config(info.filename, z.read(info), conf_dir)
                        if r:
                            stored.append(r)
        elif low.endswith(".conf") or low.endswith(".ovpn"):
            with open(path, "rb") as f:
                r = store_config(os.path.basename(path), f.read(256 * 1024), conf_dir)
            if r:
                stored.append(r)
    except (IOError, OSError, zipfile.BadZipfile):
        pass
    return stored


def import_sources():
    dirs = []
    if os.path.isdir("/media"):
        for m in sorted(os.listdir("/media")):
            base = os.path.join("/media", m)
            for d in (os.path.join(base, "protonvpn"), os.path.join(base, "ProtonVPN"), base):
                if os.path.isdir(d) and d not in dirs:
                    dirs.append(d)
    for d in ("/tmp", "/home/root"):
        if os.path.isdir(d):
            dirs.append(d)
    return dirs


def find_importable():
    found = []
    for d in import_sources():
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            low = n.lower()
            p = os.path.join(d, n)
            if not os.path.isfile(p):
                continue
            if low.endswith(".ovpn") or low.endswith(".zip"):
                found.append(p)
            elif low.endswith(".conf"):
                try:
                    with open(p, "rb") as f:
                        head = f.read(4096)
                    if b"[Interface]" in head and b"PrivateKey" in head:
                        found.append(p)
                except (IOError, OSError):
                    pass
    return found


def import_all():
    ensure_dirs()
    stored = []
    for p in find_importable():
        stored.extend(import_path(p))
    return stored


def delete_config(filename, conf_dir=CONF_DIR):
    p = os.path.join(conf_dir, _safe_name(filename))
    if os.path.isfile(p):
        os.remove(p)
        return True
    return False


def backup_configs(target_dir):
    dst = os.path.join(target_dir, "protonvpn-backup")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(CONF_DIR, dst)
    return dst
