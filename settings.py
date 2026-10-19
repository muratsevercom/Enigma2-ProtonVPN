# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import os

SETTINGS_FILE = "/etc/protonvpn/settings.json"

DEFAULTS = {
    "language": "auto",
    "autoconnect": "off",
    "autoreconnect": True,
    "killswitch": False,
    "use_vpn_dns": True,
    "block_ipv6": True,
    "fastest_proto": "wireguard",
    "fastest_free_only": False,
    "sort": "country",
    "ovpn_user": "",
    "ovpn_pass": "",
    "webpanel": False,
    "webpanel_port": 8091,
    "show_mainmenu": True,
    "favorites": [],
    "protocol": "wireguard",
    "netshield": 2,
    "vpn_accelerator": True,
    "moderate_nat": False,
    "api_user": "",
    "last_server": "",
}


class Settings(object):
    def __init__(self, path=SETTINGS_FILE):
        self.path = path
        self.data = dict(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(self.path) as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                for k, v in stored.items():
                    if k in DEFAULTS:
                        self.data[k] = v
        except (IOError, OSError, ValueError):
            pass
        return self

    def save(self):
        d = os.path.dirname(self.path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=2, sort_keys=True)
        os.rename(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def __getitem__(self, k):
        return self.data.get(k, DEFAULTS.get(k))

    def __setitem__(self, k, v):
        self.data[k] = v

    def get(self, k, default=None):
        return self.data.get(k, default)

    def toggle_favorite(self, key):
        favs = list(self.data.get("favorites") or [])
        if key in favs:
            favs.remove(key)
            on = False
        else:
            favs.append(key)
            on = True
        self.data["favorites"] = favs
        self.save()
        return on


_instance = [None]


def get_settings():
    if _instance[0] is None:
        _instance[0] = Settings()
    return _instance[0]
