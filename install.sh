#!/bin/bash
# Installs Vifte: the service (vifted), GUI, command line and the required drivers.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"
# Brukeren som startet installasjonen (via sudo eller pkexec, f.eks. ved oppdatering fra appen)
USER_NAME="${SUDO_USER:-}"
if [ -z "$USER_NAME" ] && [ -n "${PKEXEC_UID:-}" ]; then
    USER_NAME="$(id -nu "$PKEXEC_UID" 2>/dev/null || true)"
fi

echo "==> Checking dependencies"
deps_ok() {
    python3 - <<'PY' 2>/dev/null
import sys
assert sys.version_info >= (3, 10)
import gi, cairo
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
gi.require_foreign("cairo")
from gi.repository import Gtk, Adw
assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 12)
assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 5)
PY
}
if ! deps_ok; then
    if command -v zypper >/dev/null; then
        zypper --non-interactive install python3-gobject python3-gobject-cairo python3-cairo \
            typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1 || true
    elif command -v dnf >/dev/null; then
        dnf install -y python3-gobject python3-cairo gtk4 libadwaita || true
    elif command -v apt-get >/dev/null; then
        apt-get install -y python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-4.0 gir1.2-adw-1 || true
    elif command -v pacman >/dev/null; then
        pacman -S --needed --noconfirm python-gobject python-cairo gtk4 libadwaita || true
    fi
fi
if ! deps_ok; then
    echo "ERROR: Vifte needs Python >= 3.10, PyGObject with cairo support, GTK >= 4.12 and libadwaita >= 1.5."
    echo "       Install them with your package manager and run this script again."
    exit 1
fi

echo "==> Copying files"
systemctl stop vifted.service 2>/dev/null || true
rm -rf /opt/vifte
mkdir -p /opt/vifte
cp -r vifte /opt/vifte/
find /opt/vifte -name __pycache__ -prune -exec rm -rf {} +
python3 -m compileall -q /opt/vifte
cat > /usr/local/bin/vifte <<'EOS'
#!/bin/sh
PYTHONPATH=/opt/vifte exec /usr/bin/python3 -m vifte.cli "$@"
EOS
chmod 755 /usr/local/bin/vifte
install -Dm755 uninstall.sh /usr/local/bin/vifte-uninstall
install -Dm644 data/no.vifte.Vifte.desktop /usr/local/share/applications/no.vifte.Vifte.desktop
install -Dm644 vifte/icons/hicolor/scalable/apps/no.vifte.Vifte.svg \
    /usr/local/share/icons/hicolor/scalable/apps/no.vifte.Vifte.svg
install -Dm644 vifte/icons/hicolor/symbolic/apps/no.vifte.Vifte-symbolic.svg \
    /usr/local/share/icons/hicolor/symbolic/apps/no.vifte.Vifte-symbolic.svg
install -Dm644 data/no.vifte.Vifte-tray.desktop /etc/xdg/autostart/no.vifte.Vifte-tray.desktop
gtk-update-icon-cache -q /usr/local/share/icons/hicolor 2>/dev/null || true
install -Dm644 data/vifted.service /etc/systemd/system/vifted.service
install -Dm644 data/vifte-resume.service /etc/systemd/system/vifte-resume.service

echo "==> Access for the GUI (group \"vifte\")"
getent group vifte >/dev/null || groupadd -r vifte
if [ -n "$USER_NAME" ] && ! id -nG "$USER_NAME" | grep -qw vifte; then
    usermod -aG vifte "$USER_NAME"
    echo "    Added $USER_NAME to the vifte group"
fi

echo "==> Motherboard fan control driver"
have_pwm() { ls /sys/class/hwmon/hwmon*/pwm[0-9] >/dev/null 2>&1 && grep -qE '^(nct|it)[0-9]' /sys/class/hwmon/hwmon*/name; }
if ! have_pwm; then
    for mod in nct6775 nct6683 it87; do
        if modprobe "$mod" 2>/dev/null && sleep 1 && have_pwm; then
            echo "$mod" > /etc/modules-load.d/vifte.conf
            echo "    Loaded $mod; it will also be loaded at boot"
            break
        fi
        modprobe -r "$mod" 2>/dev/null || true
    done
fi
have_pwm || echo "    No Super I/O driver with PWM control found. See README (Troubleshooting)."

echo "==> Starting the service"
systemctl daemon-reload
systemctl enable --now vifted.service
systemctl enable vifte-resume.service
sleep 2
systemctl --no-pager --lines=5 status vifted.service || true
echo
echo "Done! Start \"Vifte\" from the application menu, or run: vifte"
echo "The tray icon starts automatically at next login (GNOME needs the AppIndicator extension)."
echo "Uninstall with: vifte uninstall   (or sudo vifte-uninstall)"
