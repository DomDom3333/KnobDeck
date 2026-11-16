#!/usr/bin/env bash
# KnobDeck PC app - one-time setup on Linux.
#   ./install.sh            interactive (asks before using sudo)
#   ./install.sh --yes      no questions
set -u
cd "$(dirname "$0")"
HERE="$(pwd)"
YES=0
[ "${1:-}" = "--yes" ] || [ "${1:-}" = "-y" ] && YES=1

ask() {   # ask "question" -> 0 = yes
  [ $YES = 1 ] && return 0
  read -r -p "$1 [Y/n] " a
  case "$a" in n|N|no|No) return 1 ;; *) return 0 ;; esac
}
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# ------------------------------------------------------------------ Python
find_python() {
  # Prefer the distribution's Python that has PyGObject (tray icon + settings window), then any 3.10+
  PY=""
  local c cands="/usr/bin/python3 python3 python3.13 python3.12 python3.11 python3.10"
  for c in $cands; do
    command -v "$c" >/dev/null 2>&1 || continue
    "$c" -c 'import sys, gi; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null && { PY="$c"; return; }
  done
  for c in $cands; do
    command -v "$c" >/dev/null 2>&1 || continue
    "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null && { PY="$c"; return; }
  done
}

# ------------------------------------------------------------------ system packages
. /etc/os-release 2>/dev/null || true
FAMILY="${ID:-} ${ID_LIKE:-}"
PKGS=""; INSTALL=""
case "$FAMILY" in
  *debian*|*ubuntu*)
    INSTALL="sudo apt-get install -y"
    PKGS="python3 python3-venv python3-gi gir1.2-ayatanaappindicator3-0.1 gir1.2-webkit2-4.1 playerctl xdotool wl-clipboard" ;;
  *fedora*|*rhel*|*centos*)
    INSTALL="sudo dnf install -y"
    PKGS="python3 python3-gobject libayatana-appindicator-gtk3 webkit2gtk4.1 playerctl xdotool wl-clipboard" ;;
  *arch*|*manjaro*|*endeavouros*)
    INSTALL="sudo pacman -S --needed --noconfirm"
    PKGS="python python-gobject libayatana-appindicator webkit2gtk-4.1 playerctl xdotool wl-clipboard" ;;
  *suse*)
    INSTALL="sudo zypper install -y"
    PKGS="python3 python3-gobject typelib-1_0-AyatanaAppIndicator3-0_1 typelib-1_0-WebKit2-4_1 playerctl xdotool wl-clipboard" ;;
esac

say "System packages"
echo "Optional but recommended: tray icon (AppIndicator), settings window (WebKit), media control (playerctl),"
echo "typing text (xdotool / wl-clipboard)."
if [ -n "$INSTALL" ]; then
  echo "  $INSTALL $PKGS"
  if ask "Install them now (uses sudo)?"; then
    [ "${INSTALL#sudo apt}" != "$INSTALL" ] && sudo apt-get update -qq
    # one by one, so a package missing on this release doesn't stop the rest
    for p in $PKGS; do $INSTALL "$p" >/dev/null 2>&1 && echo "  ok      $p" || echo "  skipped $p"; done
  fi
else
  echo "Unknown distribution - please install the equivalents of: python3 (3.10+), python3-venv, PyGObject,"
  echo "Ayatana AppIndicator 3, WebKit2GTK 4.1, playerctl, xdotool, wl-clipboard"
fi

find_python
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer was not found. Install it with your package manager and run this again."
  exit 1
fi
echo "Using $PY ($($PY --version))$("$PY" -c 'import gi' 2>/dev/null && echo ', with PyGObject' || echo ', without PyGObject - no tray icon / own window')"

# ------------------------------------------------------------------ device access (udev)
say "Access to the deck and the virtual keyboard"
RULES=/etc/udev/rules.d/60-knobdeck.rules
echo "The deck's USB port and /dev/uinput (used to send hotkeys) need a udev rule so your user can use them"
echo "without root. It also starts KnobDeck when you plug the deck in, and keeps ModemManager away from it."
if ask "Install $RULES (uses sudo)?"; then
  sudo mkdir -p /etc/udev/rules.d /etc/modules-load.d
  sudo tee "$RULES" >/dev/null <<'EOF'
# KnobDeck - ESP32-C3-LCDkit on USB (Serial/JTAG): access for the logged-in user, keep ModemManager away
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", TAG+="uaccess", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEM=="usb", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", TAG+="uaccess", ENV{ID_MM_DEVICE_IGNORE}="1"
# KnobDeck - start the PC app (systemd user service knobdeck.service) when the deck is plugged in
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", TAG+="systemd", ENV{SYSTEMD_USER_WANTS}+="knobdeck.service"
# KnobDeck - virtual keyboard for hotkeys and media keys
KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"
EOF
  echo uinput | sudo tee /etc/modules-load.d/knobdeck-uinput.conf >/dev/null
  sudo modprobe uinput 2>/dev/null || true
  if [ -f "$RULES" ] && command -v udevadm >/dev/null 2>&1; then
    sudo udevadm control --reload-rules && sudo udevadm trigger
    echo "  done - unplug and replug the deck once"
  else
    echo "  could not set up udev (no udev on this system?) - see README, 'Linux: permissions'"
  fi
else
  echo "  skipped. Without it: add yourself to the 'dialout' (Debian/Ubuntu/Fedora) or 'uucp' (Arch) group for"
  echo "  the deck, and hotkeys only work through xdotool on X11."
fi

# ------------------------------------------------------------------ Python environment
say "Python packages"
# --system-site-packages: the tray icon and the settings window use the distribution's PyGObject
"$PY" -m venv --system-site-packages .venv || { echo "Creating the environment failed (Debian/Ubuntu: install python3-venv)"; exit 1; }
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt || { echo "Installing packages failed - see above"; exit 1; }
if .venv/bin/python -c "import gi" 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements-window.txt >/dev/null 2>&1 \
    && echo "  settings window: yes" || echo "  settings window: no (opens in your browser)"
else
  echo "  settings window: no PyGObject - settings open in your browser"
fi

# ------------------------------------------------------------------ launchers
chmod +x KnobDeck.sh KnobDeck-console.sh 2>/dev/null
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
cat > "$APPS/knobdeck.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=KnobDeck
Comment=Stream-deck style controller (ESP32-C3-LCDkit)
Exec="$HERE/KnobDeck.sh"
Icon=$HERE/knobdeck/static/knobdeck.png
Terminal=false
Categories=Utility;
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" >/dev/null 2>&1

# start when the deck is plugged in (change in the settings: Device > Start KnobDeck automatically)
.venv/bin/python -c "from knobdeck import autostart; autostart.set_mode('plugin'); print('  autostart:', autostart.get_mode(), autostart.note())" 2>/dev/null
touch "${XDG_CONFIG_HOME:-$HOME/.config}/knobdeck/.autostart-default" 2>/dev/null

say "Done"
echo "KnobDeck now starts by itself when you plug the deck in. You can also start it from your app menu or with"
echo "./KnobDeck.sh (./KnobDeck-console.sh shows the log)."
case "${XDG_CURRENT_DESKTOP:-}" in
  *GNOME*) echo "GNOME shows tray icons only with the 'AppIndicator and KStatusNotifierItem Support' extension"
           echo "(Ubuntu has it built in). Without it KnobDeck runs fine - open the settings from the app menu." ;;
esac
