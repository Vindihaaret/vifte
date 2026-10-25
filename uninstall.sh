#!/bin/bash
# Uninstalls Vifte.
#   --purge   also delete the configurations
#   --yes     do not ask
set -u
PURGE=0; YES=0
for a in "$@"; do
    case "$a" in
        --purge) PURGE=1 ;;
        --yes|-y) YES=1 ;;
        -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    esac
done
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"

if [ "$YES" -eq 0 ] && [ -t 0 ]; then
    read -r -p "Uninstall Vifte? [y/N] " ans
    case "$ans" in j|J|y|Y|ja|yes) ;; *) echo "Cancelled"; exit 1 ;; esac
    if [ "$PURGE" -eq 0 ]; then
        read -r -p "Delete the configurations too? [y/N] " ans
        case "$ans" in j|J|y|Y|ja|yes) PURGE=1 ;; esac
    fi
fi

echo "==> Stopping the service (fans are handed back to the BIOS)"
systemctl disable --now vifted.service vifte-resume.service 2>/dev/null || true

echo "==> Removing files"
rm -f /etc/systemd/system/vifted.service /etc/systemd/system/vifte-resume.service
rm -rf /opt/vifte
rm -f /usr/local/bin/vifte /usr/local/share/applications/no.vifte.Vifte.desktop \
      /usr/local/share/icons/hicolor/scalable/apps/no.vifte.Vifte.svg \
      /usr/local/share/icons/hicolor/symbolic/apps/no.vifte.Vifte-symbolic.svg \
      /etc/xdg/autostart/no.vifte.Vifte-tray.desktop
# Lukk kjørende vindu/statusfeltikon (ikke når vi er startet fra selve programmet via pkexec)
[ -z "${PKEXEC_UID:-}" ] && pkill -f "python3 -m vifte.cli" 2>/dev/null || true
gtk-update-icon-cache -q /usr/local/share/icons/hicolor 2>/dev/null || true
if [ -f /etc/modules-load.d/vifte.conf ]; then
    # Driveren blir lastet til neste omstart, men lastes ikke automatisk lenger.
    rm -f /etc/modules-load.d/vifte.conf
fi
systemctl daemon-reload

if [ "$PURGE" -eq 1 ]; then
    echo "==> Deleting configurations"
    rm -rf /var/lib/vifte
    for uid in ${PKEXEC_UID:-} $(id -u "${SUDO_USER:-}" 2>/dev/null); do
        home=$(getent passwd "$uid" | cut -d: -f6)
        [ -n "$home" ] && rm -rf "$home/.config/vifte"
    done
    getent group vifte >/dev/null && groupdel vifte 2>/dev/null || true
else
    echo "    Configurations kept in /var/lib/vifte/Configurations"
fi

rm -f /usr/local/bin/vifte-uninstall
echo "Vifte has been uninstalled."
