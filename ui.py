# -*- coding: utf-8 -*-
from __future__ import absolute_import, division

import os
import threading

from enigma import eListboxPythonMultiContent, eTimer, getDesktop, gFont, gRGB
from enigma import RT_HALIGN_CENTER, RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_VALIGN_CENTER

from Components.ActionMap import ActionMap
from Components.ConfigList import ConfigListScreen
from Components.Label import Label
from Components.MenuList import MenuList
from Components.ScrollLabel import ScrollLabel
from Components.Sources.StaticText import StaticText
from Components.config import (ConfigInteger, ConfigPassword, ConfigSelection, ConfigText, ConfigYesNo,
                               getConfigListEntry)
from Screens.ChoiceBox import ChoiceBox
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen

try:
    from Components.MultiContent import MultiContentEntryText
except ImportError:
    def MultiContentEntryText(pos=(0, 0), size=(0, 0), font=0, flags=0, text="", color=None, color_sel=None,
                              backcolor=None, backcolor_sel=None, border_width=None, border_color=None):
        return (eListboxPythonMultiContent.TYPE_TEXT, pos[0], pos[1], size[0], size[1], font, flags, text,
                color, color_sel, backcolor, backcolor_sel, border_width, border_color)
try:
    from Components.MultiContent import MultiContentEntryPixmapAlphaBlend
except ImportError:
    def MultiContentEntryPixmapAlphaBlend(pos=(0, 0), size=(0, 0), png=None, **kw):
        return (eListboxPythonMultiContent.TYPE_PIXMAP_ALPHABLEND, pos[0], pos[1], size[0], size[1], png)
try:
    from Tools.LoadPixmap import LoadPixmap
except ImportError:
    LoadPixmap = None

from . import api, configs, engine, webpanel
from .i18n import _, lang
from .settings import get_settings

VERSION = "1.2.1"
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

ACCENT = 0x6d4aff
OK = 0x2fd58a
BAD = 0xff5c7a
WARN = 0xffc24b
TXT = 0xecebf5
MUTED = 0x9a97b0
TRACK = 0x2c2a3d
WHITE = 0xffffff


def _scale():
    try:
        return getDesktop(0).size().width() / 1920.0
    except Exception:
        return 1.0


F = _scale()


def S(v):
    return int(round(v * F))


def skin(template):
    import re
    return re.sub(r"\{(\d+)\}", lambda m: str(S(int(m.group(1)))), template)


def make_timer(fn):
    t = eTimer()
    try:
        t.callback.append(fn)
    except AttributeError:
        t._conn = t.timeout.connect(fn)
    return t


def human_bytes(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return ("%.0f %s" if u == "B" else "%.1f %s") % (n, u)
        n /= 1024.0
    return "%.1f TB" % n


def human_time(sec):
    sec = int(sec or 0)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return "%d:%02d:%02d" % (h, m, s)


ERROR_LOG = "/tmp/protonvpn_error.log"


def report_error(where):
    import traceback
    import time as _t
    txt = "%s  [%s]  v%s\n%s\n" % (_t.strftime("%Y-%m-%d %H:%M:%S"), where, VERSION, traceback.format_exc())
    try:
        with open(ERROR_LOG, "a") as f:
            f.write(txt)
    except (IOError, OSError):
        pass
    try:
        engine.log("ERROR %s: %s" % (where, traceback.format_exc().strip().splitlines()[-1]))
    except Exception:
        pass
    return txt


def load_color(load):
    return OK if load < 50 else (WARN if load < 80 else BAD)


_flags = {}


def flag(code):
    code = (code or "").lower()
    if code not in _flags:
        p = os.path.join(PLUGIN_DIR, "flags", code + ".png")
        _flags[code] = LoadPixmap(cached=True, path=p) if (LoadPixmap and os.path.isfile(p)) else None
    return _flags[code]


class Background(object):

    def __init__(self):
        self._jobs = []
        self._poll = make_timer(self._check)

    def run(self, fn, done=None):
        box = {"done": False}

        def target():
            try:
                box["result"] = fn()
            except Exception as e:
                box["result"] = e
            box["done"] = True
        th = threading.Thread(target=target)
        th.daemon = True
        self._jobs.append((box, done))
        th.start()
        self._poll.start(200, False)

    def _check(self):
        pending = []
        for box, done in self._jobs:
            if box["done"]:
                if done:
                    done(box.get("result"))
            else:
                pending.append((box, done))
        self._jobs = pending
        if not pending:
            self._poll.stop()

    def busy(self):
        return bool(self._jobs)

    def stop(self):
        self._poll.stop()
        self._jobs = []


def T(x, y, w, h, text, font=0, color=TXT, sel=WHITE, align=RT_HALIGN_LEFT, back=None, back_sel=None):
    kw = dict(pos=(S(x), S(y)), size=(S(w), S(h)), font=font, flags=align | RT_VALIGN_CENTER,
              text=text, color=color, color_sel=sel)
    if back is not None:
        kw["backcolor"] = back
        kw["backcolor_sel"] = back if back_sel is None else back_sel
    return MultiContentEntryText(**kw)


def load_bar(x, y, load, w=110):
    load = max(0, min(100, int(load or 0)))
    c = load_color(load)
    fill = max(2, int(w * load / 100.0))
    return [T(x, y, w, 8, "", back=TRACK, back_sel=0x8f78ff),
            T(x, y, fill, 8, "", back=c, back_sel=c),
            T(x + w + 10, y - 16, 70, 40, "%d%%" % load, 1, c, WHITE, RT_HALIGN_RIGHT)]


def make_list(item_h=72):
    lb = MenuList([], enableWrapAround=True, content=eListboxPythonMultiContent)
    lb.l.setFont(0, gFont("Regular", S(30)))
    lb.l.setFont(1, gFont("Regular", S(22)))
    lb.l.setFont(2, gFont("Regular", S(26)))
    lb.l.setItemHeight(S(item_h))
    return lb


def explain_error(msg):
    msg = msg or ""
    table = {
        "OPENVPN_MISSING": _("OpenVPN is not installed. Install it now from the feed?"),
        "OPENVPN_CREDENTIALS": _("Enter your OpenVPN / IKEv2 username and password in Settings.\n(account.protonvpn.com > Account > OpenVPN / IKEv2 username - not your Proton login!)"),
        "OPENVPN_AUTH_FAILED": _("OpenVPN rejected the username/password."),
    }
    if msg in table:
        return table[msg]
    if msg.startswith("CERT:"):
        return _("Could not get a VPN key from Proton:\n%s") % msg[5:].strip()
    return msg


class ConnectMixin(object):

    def _initConnect(self):
        self.settings = get_settings()
        self.engine = engine.Engine(self.settings)
        self.bg = Background()
        self.busy_text = ""
        self._progress = {}
        self._progressTimer = make_timer(self._showProgress)

    def setBusy(self, text):
        self.busy_text = text
        if "busy" in self:
            self["busy"].setText(text)

    def _showProgress(self):
        step = self._progress.get("step")
        if step:
            self.setBusy(_(step) + "  " + self._progress.get("name", ""))

    def connectTo(self, server, done_cb=None):
        if self.busy_text:
            return
        if server is None:
            self.session.open(MessageBox, _("No reachable server found."), MessageBox.TYPE_ERROR, timeout=5)
            return
        is_api = getattr(server, "source", "") == "api"
        if is_api and not server.allowed(api.max_tier()):
            self.session.open(MessageBox, _("%s needs a paid Proton VPN plan.") % server.short_id(),
                              MessageBox.TYPE_INFO, timeout=6)
            return
        uses_ovpn = server.proto == "openvpn" or (is_api and self.settings["protocol"] != "wireguard")
        if uses_ovpn:
            if not engine.openvpn_binary():
                self.session.openWithCallback(lambda yes: yes and self._installOpenVPN(server, done_cb), MessageBox,
                                              _("OpenVPN is not installed. Install it now from the feed?"),
                                              MessageBox.TYPE_YESNO)
                return
            user, pw = api.openvpn_credentials(self.settings) if is_api else (self.settings["ovpn_user"], self.settings["ovpn_pass"])
            if not user or not pw:
                self.session.open(MessageBox, explain_error("OPENVPN_CREDENTIALS"), MessageBox.TYPE_INFO)
                return
        self._progress = {"name": server.short_id(), "step": None}
        self.setBusy(_("Connecting...") + "  " + server.short_id())
        self._progressTimer.start(300, False)

        def progress(step):
            self._progress["step"] = step

        def done(res):
            self._progressTimer.stop()
            self.setBusy("")
            if isinstance(res, Exception):
                res = (False, str(res))
            ok, msg = res
            if not ok:
                self.session.open(MessageBox, _("Connection failed:\n%s") % explain_error(msg), MessageBox.TYPE_ERROR)
            if done_cb:
                done_cb(ok)
        self.bg.run(lambda: self.engine.connect(server, progress), done)

    def _installOpenVPN(self, server, done_cb):
        self.setBusy(_("Installing OpenVPN..."))

        def done(res):
            self.setBusy("")
            ok = isinstance(res, tuple) and res[0]
            self.session.open(MessageBox, _("OpenVPN installed.") if ok else _("OpenVPN could not be installed."),
                              MessageBox.TYPE_INFO if ok else MessageBox.TYPE_ERROR, timeout=5)
            if ok:
                self.connectTo(server, done_cb)
        self.bg.run(engine.install_openvpn, done)

    def disconnect(self):
        if self.busy_text:
            return
        self.setBusy(_("Disconnecting..."))

        def done(res):
            self.setBusy("")
            self.onConnectionChanged()
        self.bg.run(self.engine.disconnect, done)

    def onConnectionChanged(self):
        pass


def country_row(c, tier, lg, current_key, favs, width=1040):
    best_load = c.min_load(tier)
    access = c.accessible(tier)
    connected = current_key is not None and any(s.key == current_key for s in c.servers)
    title = configs.country_name(c.code, lg)
    if c.secure_core:
        entries = sorted(set(s.entry_cc for s in c.servers))
        sub = "Secure Core  \u00b7  " + _("via %s") % ", ".join(configs.country_name(e, lg) for e in entries)
    else:
        cities = c.cities()
        sub = (_("%d servers") % len(c.servers)) + (("  ·  " + ", ".join(cities[:3])) if cities else "")
        if len(cities) > 3:
            sub += u" …"
    row = [c.key]
    fp = flag(c.code)
    if fp:
        row.append(MultiContentEntryPixmapAlphaBlend(pos=(S(56), S(18)), size=(S(48), S(36)), png=fp))
    else:
        row.append(T(56, 0, 48, 72, c.code, 1, MUTED))
    row.append(T(14, 0, 34, 72, "*" if c.key in favs else "", 0, WARN))
    row.append(T(122, 4, 560, 38, title, 0, OK if connected else TXT))
    row.append(T(122, 40, 600, 28, sub, 1, MUTED, 0xddd6ff))
    if connected:
        row.append(T(640, 0, 180, 72, u"● " + _("Connected"), 1, OK, WHITE, RT_HALIGN_RIGHT))
    elif not access:
        row.append(T(700, 0, 120, 72, "Plus", 1, WARN, WHITE, RT_HALIGN_RIGHT))
    if best_load is not None:
        row += load_bar(840, 32, best_load)
    elif access:
        row.append(T(840, 0, 190, 72, _("Maintenance"), 1, MUTED, WHITE, RT_HALIGN_RIGHT))
    return row


def server_row(s, tier, lg, current_key, favs, show_country=False):
    is_api = getattr(s, "source", "") == "api"
    connected = s.key == current_key
    row = [s.key, T(14, 0, 34, 72, "*" if s.key in favs else "", 0, WARN)]
    if show_country:
        fp = flag(s.country)
        if fp:
            row.append(MultiContentEntryPixmapAlphaBlend(pos=(S(56), S(18)), size=(S(48), S(36)), png=fp))
        x = 122
    else:
        x = 56
    if is_api:
        title = s.short_id()
        sub_parts = []
        if show_country:
            sub_parts.append(s.title(lg))
        if s.city:
            sub_parts.append(s.city)
        sub_parts += [(_("Free") if t == "Free" else t) for t in s.tags()]
        sub = "  ·  ".join(sub_parts)
    else:
        title = s.title(lg)
        sub = s.short_id() + "  ·  " + s.proto_label()
        if s.free:
            sub += "  ·  " + _("Free")
    row.append(T(x, 4, 560, 38, title, 0, OK if connected else TXT))
    row.append(T(x, 40, 640, 28, sub, 1, MUTED, 0xddd6ff))
    if connected:
        row.append(T(640, 0, 180, 72, u"● " + _("Connected"), 1, OK, WHITE, RT_HALIGN_RIGHT))
    if is_api:
        if not s.allowed(tier):
            row.append(T(700, 0, 120, 72, "Plus", 1, WARN, WHITE, RT_HALIGN_RIGHT))
        if not s.online:
            row.append(T(840, 0, 190, 72, _("Maintenance"), 1, MUTED, WHITE, RT_HALIGN_RIGHT))
        else:
            row += load_bar(840, 32, s.load)
    if s.ping is not None:
        ptxt = u"—" if s.ping < 0 else "%d ms" % s.ping
        pcol = BAD if s.ping < 0 else (OK if s.ping < 70 else (WARN if s.ping < 150 else BAD))
        if is_api:
            if not connected:
                row.append(T(640, 0, 180, 72, ptxt, 1, pcol, WHITE, RT_HALIGN_RIGHT))
        else:
            row.append(T(880, 0, 140, 72, ptxt, 0, pcol, WHITE, RT_HALIGN_RIGHT))
    return row


MAIN_SKIN = """
<screen name="ProtonVPNMain" position="center,center" size="{1760},{940}" flags="wfNoBorder" backgroundColor="#0a0f0e17">
  <eLabel position="0,0" size="{1760},{96}" backgroundColor="#001b1a27" zPosition="-1"/>
  <eLabel position="0,{96}" size="{1760},{3}" backgroundColor="#006d4aff"/>
  <widget name="title" position="{40},{18}" size="{700},{60}" font="Regular;{44}" foregroundColor="#00ecebf5" backgroundColor="#001b1a27" transparent="1" valign="center"/>
  <widget name="account" position="{760},{18}" size="{960},{60}" font="Regular;{26}" foregroundColor="#009a97b0" backgroundColor="#001b1a27" transparent="1" halign="right" valign="center"/>

  <eLabel position="{40},{130}" size="{600},{690}" backgroundColor="#001b1a27" zPosition="-1"/>
  <widget name="state" position="{70},{150}" size="{550},{60}" font="Regular;{42}" foregroundColor="#00ff5c7a" backgroundColor="#001b1a27" transparent="1"/>
  <widget name="substate" position="{70},{212}" size="{550},{40}" font="Regular;{24}" foregroundColor="#009a97b0" backgroundColor="#001b1a27" transparent="1"/>
  <eLabel position="{70},{268}" size="{540},{2}" backgroundColor="#002c2a3d"/>
  <widget name="keys" position="{70},{290}" size="{190},{460}" font="Regular;{27}" foregroundColor="#009a97b0" backgroundColor="#001b1a27" transparent="1"/>
  <widget name="values" position="{260},{290}" size="{360},{460}" font="Regular;{27}" foregroundColor="#00ecebf5" backgroundColor="#001b1a27" transparent="1"/>
  <widget name="busy" position="{70},{750}" size="{550},{60}" font="Regular;{26}" foregroundColor="#00ffc24b" backgroundColor="#001b1a27" transparent="1"/>

  <widget name="tab0" position="{680},{126}" size="{250},{50}" font="Regular;{28}" halign="center" valign="center" foregroundColor="#009a97b0" backgroundColor="#0a0f0e17" transparent="1"/>
  <widget name="tab1" position="{940},{126}" size="{250},{50}" font="Regular;{28}" halign="center" valign="center" foregroundColor="#009a97b0" backgroundColor="#0a0f0e17" transparent="1"/>
  <widget name="tab2" position="{1200},{126}" size="{250},{50}" font="Regular;{28}" halign="center" valign="center" foregroundColor="#009a97b0" backgroundColor="#0a0f0e17" transparent="1"/>
  <widget name="tab3" position="{1460},{126}" size="{260},{50}" font="Regular;{28}" halign="center" valign="center" foregroundColor="#009a97b0" backgroundColor="#0a0f0e17" transparent="1"/>
  <eLabel position="{680},{180}" size="{1040},{2}" backgroundColor="#002c2a3d"/>
  <widget name="tabline" position="{680},{177}" size="{250},{5}" font="Regular;{4}" backgroundColor="#006d4aff"/>
  <widget name="list" position="{680},{196}" size="{1040},{648}" backgroundColor="#0a0f0e17" foregroundColor="#00ecebf5" backgroundColorSelected="#006d4aff" foregroundColorSelected="#00ffffff" scrollbarMode="showOnDemand" transparent="0"/>
  <widget name="hint" position="{700},{260}" size="{1000},{500}" font="Regular;{28}" foregroundColor="#009a97b0" backgroundColor="#0a0f0e17" transparent="1" halign="center" valign="center"/>

  <eLabel position="{40},{868}" size="{8},{44}" backgroundColor="#00e04040"/>
  <widget source="key_red" render="Label" position="{58},{868}" size="{300},{44}" font="Regular;{27}" foregroundColor="#00ecebf5" backgroundColor="#0a0f0e17" transparent="1" valign="center"/>
  <eLabel position="{370},{868}" size="{8},{44}" backgroundColor="#0030c050"/>
  <widget source="key_green" render="Label" position="{388},{868}" size="{300},{44}" font="Regular;{27}" foregroundColor="#00ecebf5" backgroundColor="#0a0f0e17" transparent="1" valign="center"/>
  <eLabel position="{700},{868}" size="{8},{44}" backgroundColor="#00e0c030"/>
  <widget source="key_yellow" render="Label" position="{718},{868}" size="{300},{44}" font="Regular;{27}" foregroundColor="#00ecebf5" backgroundColor="#0a0f0e17" transparent="1" valign="center"/>
  <eLabel position="{1030},{868}" size="{8},{44}" backgroundColor="#003070e0"/>
  <widget source="key_blue" render="Label" position="{1048},{868}" size="{260},{44}" font="Regular;{27}" foregroundColor="#00ecebf5" backgroundColor="#0a0f0e17" transparent="1" valign="center"/>
  <widget name="keyhelp" position="{1320},{868}" size="{400},{44}" font="Regular;{22}" foregroundColor="#009a97b0" backgroundColor="#0a0f0e17" transparent="1" halign="right" valign="center"/>
</screen>"""

TABS = ("countries", "securecore", "favorites", "files")


class ProtonVPNMain(ConnectMixin, Screen):
    skin = skin(MAIN_SKIN)

    def __init__(self, session):
        Screen.__init__(self, session)
        self._initConnect()
        self.tab = 0
        self.live = []
        self.files = []
        self.rows_meta = {}
        self.ipinfo = None
        self.ip_checking = False
        self.last_status = {}
        self.conf_mtime = None
        self.api_mtime = None
        self.files_filter = "all"

        self["title"] = Label("Proton VPN")
        self["account"] = Label("")
        self["state"] = Label("")
        self["substate"] = Label("")
        self["keys"] = Label("")
        self["values"] = Label("")
        self["busy"] = Label("")
        self["hint"] = Label("")
        for i in range(4):
            self["tab%d" % i] = Label("")
        self["tabline"] = Label("")
        self["keyhelp"] = Label(u"◀ ▶ " + _("tabs") + "   0 = " + _("favorite") + "   MENU")
        self["key_red"] = StaticText("")
        self["key_green"] = StaticText(_("Quick connect"))
        self["key_yellow"] = StaticText("")
        self["key_blue"] = StaticText(_("Settings"))
        self.listbox = make_list(72)
        self["list"] = self.listbox

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions", "MenuActions", "NumberActions", "EPGSelectActions", "DirectionActions"],
            {
                "ok": self.keyOK,
                "cancel": self.close,
                "red": self.disconnect,
                "green": self.keyGreen,
                "yellow": self.keyYellow,
                "blue": self.openSettings,
                "menu": self.openMenu,
                "0": self.toggleFavorite,
                "info": self.refreshIP,
                "left": lambda: self.switchTab(-1),
                "right": lambda: self.switchTab(1),
                "nextBouquet": self.listbox.pageUp,
                "prevBouquet": self.listbox.pageDown,
            }, -1)

        self.statusTimer = make_timer(self.updateStatus)
        self.onLayoutFinish.append(self.layoutFinished)
        self.onClose.append(self.cleanup)

    def layoutFinished(self):
        try:
            self._layoutFinished()
        except Exception:
            report_error("main screen start")
            self["hint"].setText(_("Error - details in /tmp/protonvpn_error.log"))
            self["hint"].show()

    def _layoutFinished(self):
        self.setTitle("Proton VPN")
        configs.ensure_dirs()
        self.reloadServers()
        if not self.live and self.files:
            self.tab = 3
        self.updateTabs()
        self.updateStatus()
        self.statusTimer.start(2000, False)
        self.refreshIP()
        if api.logged_in():
            age = api.servers_age()
            if age is None or age > 15 * 60 or not api.account():
                self.refreshServerList(quiet=True)
        elif not self.files:
            self._loginTimer = make_timer(self._deferredLogin)
            self._loginTimer.start(300, True)

    def _deferredLogin(self):
        self._loginTimer.stop()
        self.openLogin()

    def cleanup(self):
        self.statusTimer.stop()
        self._progressTimer.stop()
        self.bg.stop()

    def reloadServers(self):
        old = dict((s.key, s.ping) for s in self.files)
        self.files = configs.load_servers()
        for s in self.files:
            s.ping = old.get(s.key)
        self.live = api.load_servers() if api.logged_in() else []
        try:
            self.conf_mtime = os.stat(configs.CONF_DIR).st_mtime
        except OSError:
            self.conf_mtime = None
        self.api_mtime = api._api_cache["mtime"]
        self.buildList()

    def refreshServerList(self, quiet=False):
        if self.busy_text:
            return
        self.setBusy(_("Updating server list..."))

        def job():
            if not api.account() or not quiet:
                api.fetch_account()
            return api.fetch_servers()

        def done(r):
            self.setBusy("")
            if isinstance(r, dict) and not r.get("ok"):
                if r.get("code") == 401:
                    self.reloadServers()
                    self.openLogin()
                elif not quiet:
                    self.session.open(MessageBox, _("Could not update the server list:\n%s") % r.get("error"),
                                      MessageBox.TYPE_ERROR, timeout=8)
            self.reloadServers()
            self.updateStatus()
        self.bg.run(job, done)

    def switchTab(self, d):
        self.tab = (self.tab + d) % len(TABS)
        self.updateTabs()
        self.buildList()
        self.listbox.moveToIndex(0)

    def updateTabs(self):
        names = (_("Countries"), "Secure Core", _("Favorites"), _("Files"))
        for i, n in enumerate(names):
            self["tab%d" % i].setText(n)
            try:
                self["tab%d" % i].instance.setForegroundColor(gRGB(TXT if i == self.tab else MUTED))
            except AttributeError:
                pass
        try:
            from enigma import ePoint
            self["tabline"].instance.move(ePoint(S(680 + 260 * self.tab), S(177)))
        except Exception:
            pass
        t = TABS[self.tab]
        self["key_yellow"].setText(_("Ping test") if t == "files" else _("Connect country"))

    def current_key(self):
        return self.last_status.get("server") if self.last_status.get("connected") else None

    def buildList(self):
        lg = lang()
        favs = set(self.settings["favorites"])
        tier = api.max_tier()
        cur = self.current_key()
        sel = self.listbox.getCurrent()
        sel_key = sel[0] if sel else None
        rows, meta = [], {}
        t = TABS[self.tab]
        if t in ("countries", "securecore"):
            for c in api.countries(self.live, secure_core=(t == "securecore"), lang=lg):
                rows.append(country_row(c, tier, lg, cur, favs))
                meta[c.key] = c
        elif t == "favorites":
            cmap = {}
            for sc in (False, True):
                for c in api.countries(self.live, secure_core=sc, lang=lg):
                    cmap[c.key] = c
            smap = dict((s.key, s) for s in self.live if s.key in favs)
            smap.update(dict((s.key, s) for s in self.files if s.key in favs))
            for k in self.settings["favorites"]:
                if k in cmap:
                    rows.append(country_row(cmap[k], tier, lg, cur, favs))
                    meta[k] = cmap[k]
                elif k in smap:
                    rows.append(server_row(smap[k], tier, lg, cur, favs, show_country=True))
                    meta[k] = smap[k]
        else:
            files = self.files
            if self.files_filter == "free":
                files = [s for s in files if s.free]
            elif self.files_filter == "wg":
                files = [s for s in files if s.proto == "wireguard"]
            for s in configs.sort_servers(files, self.settings["sort"], favs, lg):
                rows.append(server_row(s, tier, lg, cur, favs, show_country=True))
                meta[s.key] = s
        self.rows_meta = meta
        self.listbox.setList(rows)
        if sel_key:
            for i, r in enumerate(rows):
                if r[0] == sel_key:
                    self.listbox.moveToIndex(i)
                    break
        self.updateHint(rows)

    def updateHint(self, rows):
        t = TABS[self.tab]
        text = ""
        if not rows:
            if t in ("countries", "securecore"):
                text = (_("Log in with your Proton account to see all servers.\n\nPress GREEN or MENU > Log in.")
                        if not api.logged_in() else _("Server list is empty. MENU > Refresh server list."))
            elif t == "favorites":
                text = _("No favorites yet.\nPress 0 on a country or server to add it here.")
            else:
                text = (_("No servers yet") + "\n\n" +
                        _("Download WireGuard configs from account.protonvpn.com > Downloads and\ncopy them to a USB stick folder 'protonvpn', or upload them from your\nbrowser: http://%s:%d")
                        % (webpanel.local_ip(), int(self.settings["webpanel_port"])) + "\n\n" + _("Press MENU to import."))
        self["hint"].setText(text)
        if text:
            self["hint"].show()
        else:
            self["hint"].hide()

    def selected(self):
        cur = self.listbox.getCurrent()
        return self.rows_meta.get(cur[0]) if cur else None

    def updateStatus(self):
        try:
            self._updateStatus()
        except Exception:
            if not getattr(self, "_status_error", False):
                self._status_error = True
                report_error("status update")

    def _updateStatus(self):
        try:
            mt = os.stat(configs.CONF_DIR).st_mtime
        except OSError:
            mt = None
        api.load_servers()
        if mt != self.conf_mtime or api._api_cache["mtime"] != self.api_mtime:
            self.reloadServers()
        st = self.engine.status()
        changed = st.get("connected") != self.last_status.get("connected") or st.get("server") != self.last_status.get("server")
        self.last_status = st
        if changed:
            self.buildList()
            if not self.bg.busy():
                self.refreshIP()
        lg = lang()
        acc = api.account() if api.logged_in() else {}
        self["account"].setText(("%s  ·  %s" % (acc.get("user", ""), acc.get("plan") or "Proton VPN")) if acc
                                else (_("Not logged in") if not api.logged_in() else ""))
        if st.get("connected"):
            self["state"].setText(_("Connected"))
            self["state"].instance.setForegroundColor(gRGB(OK))
            self["substate"].setText(_("Your traffic is protected"))
            title = configs.country_name(st.get("country"), lg)
            if st.get("entry"):
                title = "%s > %s" % (configs.country_name(st["entry"], lg), title)
            keys = [_("Server"), "", _("Protocol"), _("Public IP"), _("Location"), _("Provider"), _("Duration"),
                    _("Traffic")]
            proto = "WireGuard" if st.get("proto") == "wireguard" else "OpenVPN"
            vals = [title, st.get("label", ""), proto]
        else:
            self["state"].setText(_("Disconnected"))
            self["state"].instance.setForegroundColor(gRGB(BAD))
            self["substate"].setText(_("Internet blocked by kill switch") if st.get("killswitch")
                                     else _("Your traffic is NOT protected"))
            keys = [_("Public IP"), _("Location"), _("Provider")]
            vals = []
        info = self.ipinfo
        if self.ip_checking:
            vals += [_("checking..."), "", ""]
        elif info:
            loc = ", ".join([x for x in (info.get("city"), info.get("country")) if x])
            vals += [info.get("ip", ""), loc, (info.get("isp") or "")[:26]]
        else:
            vals += [_("unknown"), "", ""]
        if st.get("connected"):
            vals += [human_time(st.get("uptime")),
                     u"↓ %s  ↑ %s" % (human_bytes(st.get("rx")), human_bytes(st.get("tx")))]
            if st.get("domain"):
                ag = api.agent_state()
                ns = int(self.settings["netshield"] or 0)
                stats = ag.get("stats") or {}
                if ns:
                    keys.append("NetShield")
                    blocked = int(stats.get("ads-blocked", 0)) + int(stats.get("trackers-blocked", 0)) + int(stats.get("malware-blocked", 0))
                    vals.append(_("%d blocked") % blocked if stats else _("On"))
                bad = {"SoftJailed": _("Restricted"), "HardJailed": _("Blocked by Proton"),
                       "ClientCertificateExpiredError": _("Key expired"),
                       "ServerCertificateError": _("Server check failed"),
                       "ClientCertificateUnknownCA": _("Key not accepted")}
                if ag.get("state") in bad:
                    keys.append(_("Session"))
                    vals.append(bad[ag["state"]])
        keys.append(_("Kill switch"))
        vals.append(_("On") if st.get("killswitch") else _("Off"))
        self["keys"].setText("\n".join(keys))
        self["values"].setText("\n".join(vals))
        self["busy"].setText(self.busy_text)
        self["key_red"].setText(_("Disconnect") if st.get("connected") or st.get("killswitch") else "")
        self["key_green"].setText(_("Quick connect") if (api.logged_in() or self.files) else _("Log in"))

    def refreshIP(self):
        if self.ip_checking:
            return
        self.ip_checking = True

        def done(res):
            self.ip_checking = False
            self.ipinfo = res if isinstance(res, dict) else None
            self.updateStatus()
        self.bg.run(engine.public_ip_info, done)

    def onConnectionChanged(self):
        self.ipinfo = None
        self.updateStatus()
        self.refreshIP()

    def keyOK(self):
        obj = self.selected()
        if obj is None:
            return
        if isinstance(obj, api.Country):
            self.session.openWithCallback(lambda *a: self.onConnectionChanged(), ProtonVPNCountry, obj)
        else:
            self.connectTo(obj, lambda ok: self.onConnectionChanged())

    def keyGreen(self):
        if not api.logged_in() and not self.files:
            self.openLogin()
            return
        if self.busy_text:
            return
        if self.live:
            self.connectTo(api.best_server(self.live, self.settings), lambda ok: self.onConnectionChanged())
            return
        self.setBusy(_("Searching fastest server..."))

        def done(best):
            self.setBusy("")
            self.buildList()
            self.connectTo(best if isinstance(best, configs.Server) else None, lambda ok: self.onConnectionChanged())
        files = list(self.files)
        self.bg.run(lambda: engine.pick_fastest(files, self.settings), done)

    def keyYellow(self):
        t = TABS[self.tab]
        if t == "files":
            self.pingFiles()
            return
        obj = self.selected()
        if isinstance(obj, api.Country):
            best = api.best_server(obj.servers, self.settings, secure_core=obj.secure_core)
            self.connectTo(best, lambda ok: self.onConnectionChanged())
        elif obj is not None:
            self.connectTo(obj, lambda ok: self.onConnectionChanged())

    def pingFiles(self):
        if self.busy_text or not self.files:
            return
        targets = list(self.files)
        self.setBusy(_("Measuring ping to %d servers...") % len(targets))

        def done(res):
            self.setBusy("")
            self.settings["sort"] = "ping"
            self.buildList()
            self.listbox.moveToIndex(0)
        self.bg.run(lambda: engine.ping_servers(targets), done)

    def toggleFavorite(self):
        cur = self.listbox.getCurrent()
        if cur:
            self.settings.toggle_favorite(cur[0])
            self.buildList()

    def openSettings(self):
        self.session.openWithCallback(self.settingsClosed, ProtonVPNSettings)

    def settingsClosed(self, *args):
        self.settings.load()
        from .plugin import apply_webpanel
        apply_webpanel()
        self.buildList()

    def openLogin(self):
        self.session.openWithCallback(self.loginClosed, ProtonVPNCodeLogin)

    def loginClosed(self, ok=False, *args):
        self.reloadServers()
        if ok:
            self.tab = 0
            self.updateTabs()
            self.buildList()
        self.updateStatus()

    def openMenu(self):
        items = []
        if api.logged_in():
            items += [(_("Refresh server list"), "refresh"), (_("Log out") + "  (%s)" % api.account().get("user", ""), "logout")]
        else:
            items.append((_("Log in with Proton account"), "login"))
        items += [
            (_("Add / remove favorite"), "fav"),
            (_("Import configs (USB / HDD / tmp)"), "import"),
            (_("Show: all servers"), "f_all"),
            (_("Show: free servers"), "f_free"),
            (_("Show: WireGuard only"), "f_wg"),
            (_("Sort by country"), "s_country"),
            (_("Sort by ping"), "s_ping"),
            (_("Web upload panel"), "web"),
            (_("Back up configs to HDD/USB"), "backup"),
            (_("Delete selected server"), "delete"),
            (_("Show log"), "log"),
            (_("About"), "about"),
        ]
        self.session.openWithCallback(self.menuCallback, ChoiceBox, title=_("Options"), list=items)

    def menuCallback(self, choice):
        if not choice:
            return
        c = choice[1]
        if c == "refresh":
            self.refreshServerList()
        elif c == "login":
            self.openLogin()
        elif c == "logout":
            self.session.openWithCallback(lambda yes: yes and self.doLogout(), MessageBox,
                                          _("Log out from Proton VPN on this receiver?"), MessageBox.TYPE_YESNO)
        elif c == "import":
            self.doImport()
        elif c == "fav":
            self.toggleFavorite()
        elif c.startswith("f_"):
            self.files_filter = c[2:]
            self.tab = 3
            self.updateTabs()
            self.buildList()
        elif c.startswith("s_"):
            self.settings["sort"] = c[2:]
            self.settings.save()
            self.buildList()
        elif c == "web":
            if webpanel.running():
                txt = _("Web panel: http://%s:%d") % (webpanel.local_ip(), int(self.settings["webpanel_port"]))
            else:
                txt = _("Web panel is off. Enable it in Settings.")
            self.session.open(MessageBox, txt, MessageBox.TYPE_INFO)
        elif c == "backup":
            self.doBackup()
        elif c == "delete":
            s = self.selected()
            if isinstance(s, configs.Server):
                self.session.openWithCallback(lambda yes: yes and (configs.delete_config(s.filename), self.reloadServers()),
                                              MessageBox, _("Delete %s?") % s.short_id(), MessageBox.TYPE_YESNO)
        elif c == "log":
            self.session.open(ProtonVPNLog)
        elif c == "about":
            self.session.open(MessageBox, _("Proton VPN for Enigma2\nWireGuard (userspace) + OpenVPN\n\nby Murat SEVER")
                              + "\n\nv" + VERSION, MessageBox.TYPE_INFO)

    def doLogout(self):
        self.setBusy(_("Logging out..."))

        def done(r):
            self.setBusy("")
            self.reloadServers()
            self.updateStatus()
        self.bg.run(api.logout, done)

    def doImport(self):
        stored = configs.import_all()
        self.reloadServers()
        if stored:
            self.tab = 3
            self.updateTabs()
            self.buildList()
            self.session.open(MessageBox, _("%d config(s) imported.") % len(stored), MessageBox.TYPE_INFO, timeout=5)
        else:
            self.session.open(MessageBox, _("No new Proton VPN config found.\n\nSearched in:\n%s")
                              % "\n".join(configs.import_sources()[:8]), MessageBox.TYPE_INFO)

    def doBackup(self):
        targets = [d for d in ("/media/hdd", "/media/usb", "/media/mmc") if os.path.ismount(d)]
        if not targets:
            self.session.open(MessageBox, _("No HDD/USB found."), MessageBox.TYPE_ERROR, timeout=5)
            return
        try:
            dst = configs.backup_configs(targets[0])
            self.session.open(MessageBox, _("Backup saved to %s") % dst, MessageBox.TYPE_INFO, timeout=6)
        except (IOError, OSError) as e:
            self.session.open(MessageBox, str(e), MessageBox.TYPE_ERROR)


class ProtonVPNCountry(ConnectMixin, Screen):
    skin = skin("""
<screen name="ProtonVPNCountry" position="center,center" size="{1300},{940}" flags="wfNoBorder" backgroundColor="#0a0f0e17">
  <eLabel position="0,0" size="{1300},{110}" backgroundColor="#001b1a27" zPosition="-1"/>
  <eLabel position="0,{110}" size="{1300},{3}" backgroundColor="#006d4aff"/>
  <widget name="flag" position="{40},{31}" size="{48},{36}" alphatest="blend"/>
  <widget name="title" position="{108},{12}" size="{900},{56}" font="Regular;{40}" foregroundColor="#00ecebf5" backgroundColor="#001b1a27" transparent="1" valign="center"/>
  <widget name="subtitle" position="{108},{64}" size="{1150},{36}" font="Regular;{24}" foregroundColor="#009a97b0" backgroundColor="#001b1a27" transparent="1" valign="center"/>
  <widget name="list" position="{40},{130}" size="{1220},{720}" backgroundColor="#0a0f0e17" foregroundColor="#00ecebf5" backgroundColorSelected="#006d4aff" foregroundColorSelected="#00ffffff" scrollbarMode="showOnDemand"/>
  <widget name="busy" position="{40},{860}" size="{700},{60}" font="Regular;{26}" foregroundColor="#00ffc24b" backgroundColor="#0a0f0e17" transparent="1" valign="center"/>
  <eLabel position="{760},{868}" size="{8},{44}" backgroundColor="#0030c050"/>
  <widget source="key_green" render="Label" position="{778},{868}" size="{230},{44}" font="Regular;{27}" foregroundColor="#00ecebf5" backgroundColor="#0a0f0e17" transparent="1" valign="center"/>
  <eLabel position="{1020},{868}" size="{8},{44}" backgroundColor="#00e04040"/>
  <widget source="key_red" render="Label" position="{1038},{868}" size="{230},{44}" font="Regular;{27}" foregroundColor="#00ecebf5" backgroundColor="#0a0f0e17" transparent="1" valign="center"/>
</screen>""")

    def __init__(self, session, country):
        Screen.__init__(self, session)
        self._initConnect()
        self.country = country
        lg = lang()
        title = configs.country_name(country.code, lg)
        if country.secure_core:
            title = "Secure Core  ·  " + title
        self["title"] = Label(title)
        tier = api.max_tier()
        online = [s for s in country.servers if s.online]
        sub = _("%d servers") % len(country.servers)
        if online:
            sub += "  ·  " + _("lowest load %d%%") % min(s.load for s in online)
        if not country.accessible(tier):
            sub += "  ·  " + _("needs a paid plan")
        self["subtitle"] = Label(sub)
        try:
            from Components.Pixmap import Pixmap
            self["flag"] = Pixmap()
        except ImportError:
            self["flag"] = Label("")
        self["busy"] = Label("")
        self["key_green"] = StaticText(_("Fastest here"))
        self["key_red"] = StaticText(_("Disconnect"))
        self.listbox = make_list(72)
        self["list"] = self.listbox
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "NumberActions", "EPGSelectActions"], {
            "ok": self.keyOK, "cancel": self.close, "green": self.keyFastest, "red": self.disconnect,
            "0": self.toggleFavorite, "nextBouquet": self.listbox.pageUp, "prevBouquet": self.listbox.pageDown,
        }, -1)
        self.onLayoutFinish.append(self.layoutFinished)
        self.onClose.append(self.bg.stop)

    def layoutFinished(self):
        fp = flag(self.country.code)
        if fp is not None:
            try:
                self["flag"].instance.setPixmap(fp)
            except AttributeError:
                pass
        self.buildList()

    def buildList(self):
        lg = lang()
        favs = set(self.settings["favorites"])
        st = self.engine.status()
        cur = st.get("server") if st.get("connected") else None
        tier = api.max_tier()
        sel = self.listbox.getCurrent()
        rows = [server_row(s, tier, lg, cur, favs, show_country=self.country.secure_core) for s in self.country.servers]
        self.listbox.setList(rows)
        if sel:
            for i, r in enumerate(rows):
                if r[0] == sel[0]:
                    self.listbox.moveToIndex(i)
                    break

    def selected(self):
        cur = self.listbox.getCurrent()
        if not cur:
            return None
        for s in self.country.servers:
            if s.key == cur[0]:
                return s
        return None

    def keyOK(self):
        s = self.selected()
        if s:
            self.connectTo(s, lambda ok: (self.buildList(), ok and self.close(True)))

    def keyFastest(self):
        best = api.best_server(self.country.servers, self.settings, secure_core=self.country.secure_core)
        self.connectTo(best, lambda ok: (self.buildList(), ok and self.close(True)))

    def toggleFavorite(self):
        cur = self.listbox.getCurrent()
        if cur:
            self.settings.toggle_favorite(cur[0])
            self.buildList()

    def onConnectionChanged(self):
        self.buildList()


class ProtonVPNCodeLogin(Screen):
    skin = skin("""
<screen name="ProtonVPNCodeLogin" position="center,center" size="{1200},{760}" flags="wfNoBorder" backgroundColor="#0a0f0e17">
  <eLabel position="0,0" size="{1200},{90}" backgroundColor="#001b1a27" zPosition="-1"/>
  <eLabel position="0,{90}" size="{1200},{3}" backgroundColor="#006d4aff"/>
  <widget name="title" position="{40},{15}" size="{1120},{60}" font="Regular;{40}" foregroundColor="#00ecebf5" backgroundColor="#001b1a27" transparent="1" valign="center"/>
  <widget name="step1" position="{60},{120}" size="{1080},{50}" font="Regular;{28}" foregroundColor="#009a97b0" backgroundColor="#0a0f0e17" transparent="1"/>
  <widget name="url" position="{60},{172}" size="{1080},{64}" font="Regular;{44}" foregroundColor="#00a994ff" backgroundColor="#0a0f0e17" transparent="1"/>
  <widget name="step2" position="{60},{260}" size="{1080},{50}" font="Regular;{28}" foregroundColor="#009a97b0" backgroundColor="#0a0f0e17" transparent="1"/>
  <eLabel position="{60},{320}" size="{1080},{170}" backgroundColor="#001b1a27" zPosition="-1"/>
  <widget name="code" position="{60},{320}" size="{1080},{170}" font="Regular;{96}" halign="center" valign="center" foregroundColor="#00ffffff" backgroundColor="#001b1a27" transparent="1"/>
  <widget name="status" position="{60},{510}" size="{1080},{150}" font="Regular;{26}" foregroundColor="#00ffc24b" backgroundColor="#0a0f0e17" transparent="1"/>
  <eLabel position="{60},{690}" size="{8},{44}" backgroundColor="#00e04040"/>
  <widget source="key_red" render="Label" position="{78},{690}" size="{280},{44}" font="Regular;{27}" foregroundColor="#00ecebf5" backgroundColor="#0a0f0e17" transparent="1" valign="center"/>
  <eLabel position="{380},{690}" size="{8},{44}" backgroundColor="#0030c050"/>
  <widget source="key_green" render="Label" position="{398},{690}" size="{280},{44}" font="Regular;{27}" foregroundColor="#00ecebf5" backgroundColor="#0a0f0e17" transparent="1" valign="center"/>
  <eLabel position="{700},{690}" size="{8},{44}" backgroundColor="#00e0c030"/>
  <widget source="key_yellow" render="Label" position="{718},{690}" size="{420},{44}" font="Regular;{27}" foregroundColor="#00ecebf5" backgroundColor="#0a0f0e17" transparent="1" valign="center"/>
</screen>""")

    def __init__(self, session):
        Screen.__init__(self, session)
        self.bg = Background()
        self.expires = 0
        self.polling = False
        self.done = False
        self["title"] = Label(_("Sign in with a code"))
        self["step1"] = Label(_("1. On your phone or computer open:"))
        self["url"] = Label("account.proton.me/vpn/tv")
        self["step2"] = Label(_("2. Sign in to your Proton account and enter this code:"))
        self["code"] = Label("...")
        self["status"] = Label(_("Getting a code from Proton..."))
        self["key_red"] = StaticText(_("Cancel"))
        self["key_green"] = StaticText(_("New code"))
        self["key_yellow"] = StaticText(_("Sign in with password"))
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
            "cancel": self.cancel, "red": self.cancel, "green": self.newCode, "yellow": self.usePassword,
        }, -1)
        self.pollTimer = make_timer(self.poll)
        self.clockTimer = make_timer(self.tick)
        self.onLayoutFinish.append(self.newCode)
        self.onClose.append(self.cleanup)

    def cleanup(self):
        self.pollTimer.stop()
        self.clockTimer.stop()
        self.bg.stop()

    def tick(self):
        if self.done or not self.expires:
            return
        import time as _t
        if _t.time() >= self.expires:
            self.pollTimer.stop()
            self.clockTimer.stop()
            self["code"].setText("----")
            self["status"].setText(_("The code expired. Press GREEN for a new one."))
            return
        self.showWaiting()

    def cancel(self):
        self.close(False)

    def usePassword(self):
        self.pollTimer.stop()
        self.session.openWithCallback(self.passwordDone, ProtonVPNLogin)

    def passwordDone(self, ok=False, *a):
        if ok:
            self.close(True)
        elif not self.done and self.expires:
            self.pollTimer.start(3000, False)

    def newCode(self):
        if self.done:
            return
        self.pollTimer.stop()
        self.clockTimer.stop()
        self.expires = 0
        self["code"].setText("...")
        self["status"].setText(_("Getting a code from Proton..."))

        def done(r):
            if not isinstance(r, dict) or not r.get("ok"):
                err = r.get("error") if isinstance(r, dict) else str(r)
                self["status"].setText(_("Could not get a code:\n%s") % err + "\n" + _("Press GREEN to try again."))
                return
            self.expires = int(r.get("expires") or 0)
            code = r.get("code", "")
            self["code"].setText(code)
            self["url"].setText((r.get("url") or "https://account.proton.me/vpn/tv").replace("https://", ""))
            self.showWaiting()
            self.pollTimer.start(3000, False)
            self.clockTimer.start(1000, False)
        self.bg.run(api.fork_start, done)

    def showWaiting(self):
        import time as _t
        left = max(0, int(self.expires - _t.time()))
        self["status"].setText(_("Waiting for approval...  (%d:%02d)") % (left // 60, left % 60) + "\n" +
                               _("If Proton shows a captcha, solve it there - nothing to do on the receiver."))

    def poll(self):
        if self.polling or self.done:
            return
        self.polling = True

        def done(r):
            self.polling = False
            if not isinstance(r, dict):
                return
            if r.get("ok") and r.get("status") == "ok":
                self.done = True
                self.pollTimer.stop()
                self.clockTimer.stop()
                self["status"].setText(_("Signed in. Downloading server list..."))
                self.bg.run(lambda: (api.fetch_account(), api.fetch_servers()), self.afterLogin)
            elif r.get("ok"):
                pass
            elif r.get("code") == 410:
                self.pollTimer.stop()
                self.clockTimer.stop()
                self["code"].setText("----")
                self["status"].setText(_("The code expired. Press GREEN for a new one."))
            else:
                self["status"].setText(_("Login failed:\n%s") % r.get("error"))
        self.bg.run(api.fork_poll, done)

    def afterLogin(self, res):
        a, s = res if isinstance(res, tuple) else ({}, {"ok": False, "error": str(res)})
        if not s.get("ok"):
            self["status"].setText(_("Could not update the server list:\n%s") % s.get("error"))
            return
        self.close(True)

class ProtonVPNLogin(ConfigListScreen, Screen):
    skin = skin("""
<screen name="ProtonVPNLogin" position="center,center" size="{1100},{700}" flags="wfNoBorder" backgroundColor="#0a0f0e17">
  <eLabel position="0,0" size="{1100},{90}" backgroundColor="#001b1a27" zPosition="-1"/>
  <eLabel position="0,{90}" size="{1100},{3}" backgroundColor="#006d4aff"/>
  <widget name="title" position="{40},{15}" size="{1020},{60}" font="Regular;{40}" foregroundColor="#00ecebf5" backgroundColor="#001b1a27" transparent="1" valign="center"/>
  <widget name="intro" position="{40},{115}" size="{1020},{110}" font="Regular;{25}" foregroundColor="#009a97b0" backgroundColor="#0a0f0e17" transparent="1"/>
  <widget name="config" position="{40},{240}" size="{1020},{130}" font="Regular;{30}" itemHeight="{60}" backgroundColor="#0a0f0e17" foregroundColor="#00ecebf5" backgroundColorSelected="#006d4aff" foregroundColorSelected="#00ffffff"/>
  <widget name="status" position="{40},{390}" size="{1020},{200}" font="Regular;{26}" foregroundColor="#00ffc24b" backgroundColor="#0a0f0e17" transparent="1"/>
  <eLabel position="{40},{625}" size="{8},{44}" backgroundColor="#00e04040"/>
  <widget source="key_red" render="Label" position="{58},{625}" size="{300},{44}" font="Regular;{27}" foregroundColor="#00ecebf5" backgroundColor="#0a0f0e17" transparent="1" valign="center"/>
  <eLabel position="{380},{625}" size="{8},{44}" backgroundColor="#0030c050"/>
  <widget source="key_green" render="Label" position="{398},{625}" size="{300},{44}" font="Regular;{27}" foregroundColor="#00ecebf5" backgroundColor="#0a0f0e17" transparent="1" valign="center"/>
</screen>""")

    def __init__(self, session):
        Screen.__init__(self, session)
        self.settings = get_settings()
        self.bg = Background()
        self.working = False
        self["title"] = Label(_("Log in with Proton account"))
        self["intro"] = Label(_("Use the same username and password as in the Proton VPN app.\nYour password is only used to sign in and is never stored on the receiver."))
        self["status"] = Label("")
        self["key_red"] = StaticText(_("Cancel"))
        self["key_green"] = StaticText(_("Log in"))
        self.c_user = ConfigText(default=self.settings["api_user"] or "", fixed_size=False)
        self.c_pass = ConfigPassword(default="", fixed_size=False)
        ConfigListScreen.__init__(self, [
            getConfigListEntry(_("Username or e-mail"), self.c_user),
            getConfigListEntry(_("Password"), self.c_pass),
        ], session=session)
        self["setupActions"] = ActionMap(["OkCancelActions", "ColorActions"], {
            "ok": self.keyOK, "cancel": self.cancel, "red": self.cancel, "green": self.login,
        }, -2)
        self.onClose.append(self.bg.stop)

    def keyOK(self):
        cur = self["config"].getCurrent()
        if cur and isinstance(cur[1], ConfigText):
            try:
                from Screens.VirtualKeyBoard import VirtualKeyBoard
                self.session.openWithCallback(lambda t: t is not None and (cur[1].setValue(t), self["config"].invalidateCurrent()),
                                              VirtualKeyBoard, title=cur[0], text=cur[1].value)
            except ImportError:
                pass

    def cancel(self):
        if not self.working:
            self.close(False)

    def login(self):
        user, pw = self.c_user.value.strip(), self.c_pass.value
        if self.working or not user or not pw:
            return
        self.working = True
        self["status"].setText(_("Signing in..."))
        self.settings["api_user"] = user
        self.settings.save()
        self.bg.run(lambda: api.login(user, pw), self.loginDone)

    def loginDone(self, r):
        self.working = False
        self.c_pass.value = ""
        self["config"].invalidateCurrent() if hasattr(self["config"], "invalidateCurrent") else None
        if not isinstance(r, dict):
            r = {"ok": False, "error": str(r)}
        if r.get("ok") and r.get("status") == "2fa":
            self["status"].setText(_("Enter the 6-digit code from your authenticator app."))
            try:
                from Screens.VirtualKeyBoard import VirtualKeyBoard
                self.session.openWithCallback(self.twofa, VirtualKeyBoard, title=_("Two-factor code"), text="")
            except ImportError:
                pass
            return
        if r.get("ok"):
            self.afterLogin()
            return
        self["status"].setText(self.errorText(r))

    def twofa(self, code):
        if not code:
            self["status"].setText(_("Login cancelled."))
            api.logout()
            return
        self.working = True
        self["status"].setText(_("Checking code..."))
        self.bg.run(lambda: api.submit_2fa(code.strip()), self.twofaDone)

    def twofaDone(self, r):
        self.working = False
        if isinstance(r, dict) and r.get("ok"):
            self.afterLogin()
        else:
            self["status"].setText(self.errorText(r if isinstance(r, dict) else {"error": str(r)}) + "\n" +
                                   _("Press GREEN to try again."))

    def afterLogin(self):
        self.working = True
        self["status"].setText(_("Signed in. Downloading server list..."))

        def job():
            a = api.fetch_account()
            s = api.fetch_servers()
            return a, s

        def done(res):
            self.working = False
            a, s = res if isinstance(res, tuple) else ({}, {"ok": False, "error": str(res)})
            if not s.get("ok"):
                self["status"].setText(_("Could not update the server list:\n%s") % s.get("error"))
                return
            self.close(True)
        self.bg.run(job, done)

    def errorText(self, r):
        code, err = r.get("code"), r.get("error") or ""
        if code == 8002:
            return _("Wrong username or password.")
        if code == 9001 or err == "captcha":
            return _("Proton asks for a captcha for password sign-in on this device.\nPress RED and use the code sign-in instead - the captcha is then solved on your phone.")
        if "FIDO2" in err:
            return _("Only a security key is set up as 2FA. Add an authenticator app in your Proton account to log in here.")
        return _("Login failed:\n%s") % err


class ProtonVPNSettings(ConfigListScreen, Screen):
    skin = skin("""
<screen name="ProtonVPNSettings" position="center,center" size="{1300},{900}" flags="wfNoBorder" backgroundColor="#0a0f0e17">
  <eLabel position="0,0" size="{1300},{90}" backgroundColor="#001b1a27" zPosition="-1"/>
  <eLabel position="0,{90}" size="{1300},{3}" backgroundColor="#006d4aff"/>
  <widget name="title" position="{40},{15}" size="{1200},{60}" font="Regular;{40}" foregroundColor="#00ecebf5" backgroundColor="#001b1a27" transparent="1" valign="center"/>
  <widget name="config" position="{40},{115}" size="{1220},{672}" font="Regular;{30}" itemHeight="{56}" backgroundColor="#0a0f0e17" foregroundColor="#00ecebf5" backgroundColorSelected="#006d4aff" foregroundColorSelected="#00ffffff" scrollbarMode="showOnDemand"/>
  <widget name="note" position="{40},{790}" size="{1220},{44}" font="Regular;{24}" foregroundColor="#00ffc24b" backgroundColor="#0a0f0e17" transparent="1"/>
  <eLabel position="{40},{840}" size="{8},{40}" backgroundColor="#00e04040"/>
  <widget source="key_red" render="Label" position="{58},{840}" size="{300},{40}" font="Regular;{27}" foregroundColor="#00ecebf5" backgroundColor="#0a0f0e17" transparent="1" valign="center"/>
  <eLabel position="{380},{840}" size="{8},{40}" backgroundColor="#0030c050"/>
  <widget source="key_green" render="Label" position="{398},{840}" size="{300},{40}" font="Regular;{27}" foregroundColor="#00ecebf5" backgroundColor="#0a0f0e17" transparent="1" valign="center"/>
</screen>""")

    def __init__(self, session):
        Screen.__init__(self, session)
        self.settings = get_settings()
        st = self.settings
        self["title"] = Label(_("Settings"))
        self["note"] = Label("")
        self["key_red"] = StaticText(_("Cancel"))
        self["key_green"] = StaticText(_("Save"))

        self.c_proto = ConfigSelection([("wireguard", "WireGuard"), ("openvpn-udp", "OpenVPN UDP"),
                                        ("openvpn-tcp", "OpenVPN TCP")], st["protocol"])
        self.c_ns = ConfigSelection([("0", _("Off")), ("1", _("Block malware")),
                                     ("2", _("Block malware, ads & trackers"))], str(st["netshield"]))
        self.c_accel = ConfigYesNo(bool(st["vpn_accelerator"]))
        self.c_mnat = ConfigYesNo(bool(st["moderate_nat"]))
        self.c_lang = ConfigSelection([("auto", _("Automatic")), ("tr", "Türkçe"), ("en", "English")], st["language"])
        self.c_auto = ConfigSelection([("off", _("Off")), ("last", _("Last server")), ("fastest", _("Fastest server")),
                                       ("favorite", _("Fastest favorite"))], st["autoconnect"])
        self.c_reconnect = ConfigYesNo(bool(st["autoreconnect"]))
        self.c_ks = ConfigYesNo(bool(st["killswitch"]))
        self.c_dns = ConfigYesNo(bool(st["use_vpn_dns"]))
        self.c_v6 = ConfigYesNo(bool(st["block_ipv6"]))
        self.c_ffree = ConfigYesNo(bool(st["fastest_free_only"]))
        self.c_user = ConfigText(default=st["ovpn_user"] or "", fixed_size=False)
        self.c_pass = ConfigPassword(default=st["ovpn_pass"] or "", fixed_size=False)
        self.c_web = ConfigYesNo(bool(st["webpanel"]))
        self.c_port = ConfigInteger(int(st["webpanel_port"] or 8091), (1024, 65535))
        self.c_mainmenu = ConfigYesNo(bool(st["show_mainmenu"]))

        lst = [
            getConfigListEntry(_("Protocol"), self.c_proto),
            getConfigListEntry("NetShield", self.c_ns),
            getConfigListEntry("VPN Accelerator", self.c_accel),
            getConfigListEntry(_("Moderate NAT (gaming / calls)"), self.c_mnat),
            getConfigListEntry(_("Kill switch (block internet if VPN drops)"), self.c_ks),
            getConfigListEntry(_("Auto-connect at boot"), self.c_auto),
            getConfigListEntry(_("Reconnect automatically"), self.c_reconnect),
            getConfigListEntry(_("Use Proton DNS (prevents DNS leaks)"), self.c_dns),
            getConfigListEntry(_("Block IPv6 leaks"), self.c_v6),
            getConfigListEntry(_("Quick connect: free servers only"), self.c_ffree),
            getConfigListEntry(_("Language"), self.c_lang),
            getConfigListEntry(_("Web upload panel"), self.c_web),
            getConfigListEntry(_("Web upload panel port"), self.c_port),
            getConfigListEntry(_("Show in main menu"), self.c_mainmenu),
            getConfigListEntry(_("OpenVPN username (config files)"), self.c_user),
            getConfigListEntry(_("OpenVPN password (config files)"), self.c_pass),
        ]
        ConfigListScreen.__init__(self, lst, session=session)
        self["setupActions"] = ActionMap(["OkCancelActions", "ColorActions"], {
            "ok": self.keyOK, "cancel": self.cancel, "red": self.cancel, "green": self.save,
        }, -2)

    def keyOK(self):
        cur = self["config"].getCurrent()
        if cur and isinstance(cur[1], ConfigText):
            try:
                from Screens.VirtualKeyBoard import VirtualKeyBoard
                self.session.openWithCallback(lambda t: t is not None and (cur[1].setValue(t), self["config"].invalidateCurrent()),
                                              VirtualKeyBoard, title=cur[0], text=cur[1].value)
            except ImportError:
                pass

    def save(self):
        st = self.settings
        if self.c_ks.value and not engine.killswitch_supported():
            self["note"].setText(_("Kill switch is not supported on this image (iptables missing)."))
            self.c_ks.value = False
            self["config"].invalidateCurrent()
            return
        relabel = self.c_lang.value != st["language"] or self.c_mainmenu.value != st["show_mainmenu"]
        features_changed = (self.c_proto.value != st["protocol"] or int(self.c_ns.value) != int(st["netshield"])
                            or self.c_accel.value != st["vpn_accelerator"] or self.c_mnat.value != st["moderate_nat"])
        st["protocol"] = self.c_proto.value
        st["netshield"] = int(self.c_ns.value)
        st["vpn_accelerator"] = self.c_accel.value
        st["moderate_nat"] = self.c_mnat.value
        st["language"] = self.c_lang.value
        st["autoconnect"] = self.c_auto.value
        st["autoreconnect"] = self.c_reconnect.value
        st["use_vpn_dns"] = self.c_dns.value
        st["block_ipv6"] = self.c_v6.value
        st["fastest_free_only"] = self.c_ffree.value
        st["ovpn_user"] = self.c_user.value.strip()
        st["ovpn_pass"] = self.c_pass.value
        st["webpanel"] = self.c_web.value
        st["webpanel_port"] = int(self.c_port.value)
        st["show_mainmenu"] = self.c_mainmenu.value
        ks_changed = bool(st["killswitch"]) != bool(self.c_ks.value)
        st["killswitch"] = self.c_ks.value
        st.save()
        if ks_changed:
            status = engine.Engine(st).status()
            if not self.c_ks.value:
                engine.killswitch_off()
            elif status.get("connected"):
                ep, port = status["endpoint"], status["endpoint_port"]
                eps = [(ep, port, "udp")] if status["proto"] == "wireguard" else [(ep, port, "udp"), (ep, port, "tcp")]
                engine.killswitch_on(status["iface"], eps)
        msgs = []
        if features_changed and engine.Engine(st).status().get("connected"):
            msgs.append(_("Protocol / NetShield changes apply on the next connection."))
        if relabel:
            msgs.append(_("Settings saved. Restart the GUI to apply the language / menu change."))
        if msgs:
            self.session.openWithCallback(lambda *a: self.close(True), MessageBox, "\n\n".join(msgs),
                                          MessageBox.TYPE_INFO, timeout=6)
        else:
            self.close(True)

    def cancel(self):
        self.close(False)


class ProtonVPNLog(Screen):
    skin = skin("""
<screen name="ProtonVPNLog" position="center,center" size="{1600},{860}" flags="wfNoBorder" backgroundColor="#0a0f0e17">
  <eLabel position="0,0" size="{1600},{80}" backgroundColor="#001b1a27" zPosition="-1"/>
  <widget name="title" position="{40},{10}" size="{1500},{60}" font="Regular;{36}" foregroundColor="#00ecebf5" backgroundColor="#001b1a27" transparent="1" valign="center"/>
  <widget name="text" position="{40},{100}" size="{1520},{740}" font="Regular;{24}" foregroundColor="#00ecebf5" backgroundColor="#0a0f0e17"/>
</screen>""")

    def __init__(self, session):
        Screen.__init__(self, session)
        self["title"] = Label(_("Show log"))
        parts = []
        for name in ("protonvpn.log", "api.log", "wireguard.log", "openvpn.log", "agent.log"):
            txt = engine.read_file(os.path.join(engine.RUN_DIR, name)).strip()
            if txt:
                parts.append("=== %s ===\n%s" % (name, "\n".join(txt.splitlines()[-60:])))
        self["text"] = ScrollLabel("\n\n".join(parts) or "-")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"], {
            "ok": self.close, "cancel": self.close,
            "up": self["text"].pageUp, "down": self["text"].pageDown,
            "left": self["text"].pageUp, "right": self["text"].pageDown,
        }, -1)
