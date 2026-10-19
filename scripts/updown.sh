#!/bin/sh
[ "$script_type" = "up" ] || exit 0
[ "$PVPN_DNS" = "1" ] || exit 0
DNS=""
i=1
while :; do
    eval opt=\"\$foreign_option_$i\"
    [ -z "$opt" ] && break
    case "$opt" in
        "dhcp-option DNS "*) DNS="$DNS ${opt#dhcp-option DNS }" ;;
    esac
    i=$((i + 1))
done
if [ -n "$DNS" ]; then
    : > /etc/resolv.conf.pvpn
    for d in $DNS; do echo "nameserver $d" >> /etc/resolv.conf.pvpn; done
    cat /etc/resolv.conf.pvpn > /etc/resolv.conf
    rm -f /etc/resolv.conf.pvpn
fi
exit 0
