# Proton VPN for Enigma2 — v1.1.0 (Murat SEVER)

<img width="1920" height="1080" alt="image" src="https://github.com/user-attachments/assets/26c04427-0248-4c7a-9070-7d8bf156bdee" />


## Türkçe
**Kurulum:** `opkg install /tmp/enigma2-plugin-extensions-protonvpn_1.2.1_all.ipk`
(DreamOS: `dpkg -i ...deb; apt-get -f install`) → arayüzü yeniden başlatın.

**Hesapla kullanım (Android uygulaması gibi):** Eklentiyi açın → YEŞİL → ekranda çıkan kodu telefondan account.proton.me/vpn/tv adresine girin (captcha olursa telefonda çözülür). İsterseniz SARI ile şifreyle de girebilirsiniz.
Proton kullanıcı adı ve şifrenizi girin; 2FA açıksa doğrulama uygulamasındaki 6 haneli kodu girin.
Şifre cihazda saklanmaz, yalnızca oturum anahtarı tutulur. Sunucu listesi (doluluk oranlarıyla) otomatik iner.

- **Sekmeler (◀ ▶):** Ülkeler · Secure Core · Favoriler · Dosyalar
- **OK:** ülkenin sunucularını aç / seçili sunucuya bağlan · **YEŞİL:** Hızlı bağlan
- **SARI:** seçili ülkeye bağlan (dosyalar sekmesinde ping testi) · **KIRMIZI:** bağlantıyı kes
- **MAVİ:** Ayarlar (Protokol, NetShield, VPN Accelerator, Moderate NAT, kill switch...) · **0:** favori · **MENU:** seçenekler

**Config dosyası yöntemi (hesapsız):** account.protonvpn.com → Downloads → WireGuard configuration dosyalarını
USB'de `protonvpn` klasörüne koyup MENU → *Config içe aktar*, ya da web panelinden (`http://cihaz-ip:8091`) yükleyin.

**Komut satırı:** `python3 /usr/lib/enigma2/python/Plugins/Extensions/ProtonVPN/engine.py status|list|connect <TR#7|fastest>|disconnect`

## English
Install, restart the GUI, press GREEN to sign in with your Proton account (TOTP 2FA supported).
Countries / Secure Core / Favorites / Files tabs with live load, quick connect, NetShield, VPN Accelerator,
kill switch, auto-connect and auto-reconnect. Config files (.conf/.ovpn) still work without an account.

## Notes
- This is an unofficial client. It uses Proton's open-source libraries the same way the official apps do
  (SRP login via go-srp, the Local Agent and UDP ping from go-vpn-lib). It identifies itself honestly
  (`x-pm-appversion: Other`); Proton can change or restrict its API at any time.
- WireGuard runs in userspace inside the bundled `protonvpn` helper (no kernel module, only TUN needed).
- Files: `/etc/protonvpn` (session, key, settings, configs — kept on uninstall), logs in `/var/run/protonvpn`.
- Licenses: plugin GPLv2; helper source in `src/` (uses go-vpn-lib GPLv3, go-srp MIT, wireguard-go MIT);
  flags from flag-icons (MIT).
