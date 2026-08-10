#!/usr/bin/env bash
# Install Tunnels Manager for the current user. No sudo, nothing outside $HOME.
#
# The desktop application id defaults to the one this project publishes. A fork can pick
# its own without editing any source file:
#
#   APP_ID=com.example.MyTunnels ./install.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Keep this default in step with DEFAULT_APP_ID in tunnels_manager/__init__.py.
DEFAULT_APP_ID="$(sed -n 's/^DEFAULT_APP_ID = "\(.*\)"$/\1/p' "$SRC/tunnels_manager/__init__.py")"
APP_ID="${APP_ID:-$DEFAULT_APP_ID}"

BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/tunnels-manager"
LAUNCHER="$BIN_DIR/tunnels-manager"

echo "==> Checking dependencies"
missing=()
python3 -c "import gi" 2>/dev/null || missing+=("python3-gi")
python3 -c "import gi; gi.require_version('Gtk','4.0')" 2>/dev/null \
  || missing+=("libgtk-4-1 gir1.2-gtk-4.0")
python3 -c "import gi; gi.require_version('Adw','1')" 2>/dev/null || missing+=("gir1.2-adw-1")
python3 -c "import yaml" 2>/dev/null || missing+=("python3-yaml")
if [ ${#missing[@]} -gt 0 ]; then
  # These are the Python bindings and typelibs for the GTK libraries your desktop already
  # ships; they are not available as pip wheels.
  echo "    Missing the GTK bindings. On Debian or Ubuntu:"
  echo "    sudo apt install ${missing[*]}"
  exit 1
fi
echo "    All good: nothing to install."
command -v gcloud >/dev/null || echo "    Note: gcloud is not in PATH, so IAP tunnels will fail."

echo "==> Installing the launcher in $BIN_DIR"
mkdir -p "$BIN_DIR"
chmod +x "$SRC/tunnels-manager"
ln -sf "$SRC/tunnels-manager" "$LAUNCHER"

echo "==> Installing the icon and the desktop entry as $APP_ID"
mkdir -p "$ICON_DIR" "$DESKTOP_DIR"
cp "$SRC/data/icon.svg" "$ICON_DIR/$APP_ID.svg"

# A non-default id has to reach the application too, so its D-Bus name, its icon and its
# window all agree with the file names installed above.
if [ "$APP_ID" = "$DEFAULT_APP_ID" ]; then
  EXEC="$LAUNCHER"
else
  EXEC="env TUNNELS_MANAGER_APP_ID=$APP_ID $LAUNCHER"
fi
sed -e "s|@EXEC@|$EXEC|" -e "s|@APP_ID@|$APP_ID|g" \
  "$SRC/data/tunnels-manager.desktop.in" > "$DESKTOP_DIR/$APP_ID.desktop"

command -v update-desktop-database >/dev/null && update-desktop-database "$DESKTOP_DIR" || true
command -v gtk-update-icon-cache >/dev/null \
  && gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true

if [ -f "$CONFIG_DIR/tunnels.yaml" ]; then
  echo "==> Keeping your configuration at $CONFIG_DIR/tunnels.yaml"
else
  echo "==> On first run the configuration is created at $CONFIG_DIR/tunnels.yaml"
  echo "    from tunnels.dist.yaml"
fi

echo
echo "Done. Search for \"Tunnels Manager\" in your launcher, or run: tunnels-manager"
