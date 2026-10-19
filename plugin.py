# -*- coding: utf-8 -*-
from __future__ import absolute_import

import threading

from Plugins.Plugin import PluginDescriptor

from . import configs, engine, webpanel
from .i18n import _
from .settings import get_settings

WATCHDOG_INTERVAL = 30 * 1000


def _popup(text):
    try:
        from Screens.MessageBox import MessageBox
        from Tools import Notifications
        Notifications.AddPopup(text, MessageBox.TYPE_INFO, 5, "ProtonVPN")
    except Exception:
        pass


class Watchdog(object):

    def __init__(self):
        from .ui import make_timer
        self.timer = make_timer(self.check)
        self.failures = 0
        self.ticks = 0
        self.working = False

    def start(self):
        self.timer.start(WATCHDOG_INTERVAL, False)

    def check(self):
        st = get_settings()
        if self.working or engine._lock.locked():
            return
        reason = engine.Engine(st).health()
        if reason in (None, "ok"):
            self.failures = 0
            self.ticks += 1
            if reason == "ok" and self.ticks % 120 == 1:
                t = threading.Thread(target=engine.maintain, args=(st,))
                t.daemon = True
                t.start()
            return
        engine.log("watchdog: %s" % reason)
        if not st["autoreconnect"]:
            return
        state = engine.load_state() or {}
        key = state.get("server")
        self.working = True

        def job():
            try:
                servers = engine.all_servers()
                target = None
                if self.failures < 2:
                    target = engine.find_server(key) if key else None
                if target is None:
                    others = [s for s in servers if s.key != key] or servers
                    target = engine.pick_fastest(others, st)
                if target is None:
                    self.failures += 1
                    return
                _popup(_("Connection lost - reconnecting to %s") % target.short_id())
                ok, msg = engine.Engine(st).connect(target)
                self.failures = 0 if ok else self.failures + 1
            finally:
                self.working = False
        t = threading.Thread(target=job)
        t.daemon = True
        t.start()


_watchdog = [None]


def apply_webpanel():
    st = get_settings()
    if st["webpanel"]:
        webpanel.start(st["webpanel_port"])
    else:
        webpanel.stop()


def sessionstart(reason, **kwargs):
    try:
        _sessionstart(reason, **kwargs)
    except Exception:
        import traceback
        try:
            with open("/tmp/protonvpn_error.log", "a") as f:
                f.write("[sessionstart]\n%s\n" % traceback.format_exc())
        except (IOError, OSError):
            pass


def _sessionstart(reason, **kwargs):
    if reason != 0 or _watchdog[0] is not None:
        return
    configs.ensure_dirs()
    st = get_settings()
    if engine.load_state() is None:
        engine.dns_restore()
        engine.ipv6_restore()
        if not st["killswitch"]:
            engine.killswitch_off()
    apply_webpanel()
    _watchdog[0] = Watchdog()
    _watchdog[0].start()
    if st["autoconnect"] != "off" and engine.load_state() is None:
        def boot():
            ok, msg = engine.autoconnect(st)
            engine.log("autoconnect: %s %s" % (ok, msg))
            if ok:
                _popup(_("Proton VPN: connected to %s") % msg)
        t = threading.Thread(target=boot)
        t.daemon = True
        t.start()


def _show_error(session, where):
    import traceback
    txt = traceback.format_exc()
    try:
        with open("/tmp/protonvpn_error.log", "a") as f:
            f.write("[%s]\n%s\n" % (where, txt))
    except (IOError, OSError):
        pass
    from Screens.MessageBox import MessageBox
    lines = txt.strip().splitlines()
    session.open(MessageBox, "Proton VPN error (%s):\n\n%s\n\n/tmp/protonvpn_error.log" % (where, "\n".join(lines[-6:])),
                 MessageBox.TYPE_ERROR)


def main(session, **kwargs):
    try:
        from .ui import ProtonVPNMain
        session.open(ProtonVPNMain)
    except Exception:
        _show_error(session, "open")


def quick_toggle(session, **kwargs):
    from Screens.MessageBox import MessageBox
    eng = engine.Engine()
    if eng.status().get("connected"):
        threading.Thread(target=eng.disconnect).start()
        session.open(MessageBox, _("Proton VPN: disconnected"), MessageBox.TYPE_INFO, timeout=3)
        return
    st = get_settings()

    def job():
        target = (engine.find_server(st["last_server"]) if st["last_server"] else None) or \
            engine.pick_fastest(engine.all_servers(), st)
        if target:
            ok, msg = eng.connect(target)
            _popup((_("Proton VPN: connected to %s") % msg) if ok else (_("Connection failed:\n%s") % msg))
    threading.Thread(target=job).start()
    session.open(MessageBox, _("Connecting..."), MessageBox.TYPE_INFO, timeout=3)


def menu(menuid, **kwargs):
    if menuid == "mainmenu" and get_settings()["show_mainmenu"]:
        return [("Proton VPN", main, "protonvpn", 46)]
    if menuid == "network":
        return [("Proton VPN", main, "protonvpn_net", 60)]
    return []


def Plugins(**kwargs):
    return [
        PluginDescriptor(name="Proton VPN", description=_("Manage Proton VPN connections"),
                         where=PluginDescriptor.WHERE_PLUGINMENU, icon="plugin.png", fnc=main),
        PluginDescriptor(name="Proton VPN", description=_("Manage Proton VPN connections"),
                         where=PluginDescriptor.WHERE_EXTENSIONSMENU, fnc=main),
        PluginDescriptor(name="Proton VPN: " + _("Quick connect / disconnect"),
                         where=PluginDescriptor.WHERE_EXTENSIONSMENU, fnc=quick_toggle),
        PluginDescriptor(name="Proton VPN", where=PluginDescriptor.WHERE_MENU, fnc=menu),
        PluginDescriptor(where=PluginDescriptor.WHERE_SESSIONSTART, fnc=sessionstart),
    ]
