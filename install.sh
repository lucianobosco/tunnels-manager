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

note_config() {
  if [ -f "$CONFIG_DIR/tunnels.yaml" ]; then
    echo "==> Keeping your configuration at $CONFIG_DIR/tunnels.yaml"
  else
    echo "==> On first run the configuration is created at $CONFIG_DIR/tunnels.yaml"
    echo "    from tunnels.dist.yaml"
  fi
}

# A non-default id has to reach the application too, so its D-Bus name, its icon and its
# window all agree with the file names installed for it.
app_id_env() {
  [ "$APP_ID" = "$DEFAULT_APP_ID" ] || echo "TUNNELS_MANAGER_APP_ID=$APP_ID"
}

install_macos() {
  # GTK and PyGObject come from Homebrew. PyYAML does not (there is no formula, and
  # Homebrew's Python refuses pip), so the app runs from a small venv that sees
  # PyGObject through --system-site-packages and gets PyYAML from pip.
  local share="$HOME/.local/share/tunnels-manager"
  local venv="$share/venv"
  local app="$HOME/Applications/Tunnels Manager.app"

  echo "==> Checking dependencies"
  if ! command -v brew >/dev/null; then
    echo "    Homebrew is needed for GTK 4: https://brew.sh"
    exit 1
  fi
  local python
  python="$(brew --prefix)/bin/python3"
  if ! "$python" -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')" \
    2>/dev/null; then
    echo "    Missing GTK 4, libadwaita or their Python bindings:"
    echo "    brew install gtk4 libadwaita pygobject3"
    exit 1
  fi
  echo "    All good."
  command -v gcloud >/dev/null || echo "    Note: gcloud is not in PATH, so IAP tunnels will fail."

  echo "==> Preparing the Python environment in $venv"
  # A Homebrew Python upgrade leaves the old venv pointing at nothing: rebuild it then.
  if ! "$venv/bin/python" -c "import gi, yaml" 2>/dev/null; then
    rm -rf "$venv"
    "$python" -m venv --system-site-packages "$venv"
    "$venv/bin/python" -m pip install --quiet --disable-pip-version-check "PyYAML>=6.0"
  fi

  echo "==> Installing the launcher in $BIN_DIR"
  mkdir -p "$BIN_DIR"
  rm -f "$LAUNCHER"
  cat >"$LAUNCHER" <<EOF
#!/bin/sh
exec env $(app_id_env) "$venv/bin/python" "$SRC/tunnels-manager" "\$@"
EOF
  chmod +x "$LAUNCHER"

  echo "==> Installing $app"
  rm -rf "$app"
  mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
  local version
  version="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$SRC/pyproject.toml")"
  sed -e "s|@APP_ID@|$APP_ID|g" -e "s|@VERSION@|$version|g" \
    "$SRC/data/Info.plist.in" >"$app/Contents/Info.plist"
  # Finder starts apps with a bare PATH (/usr/bin:/bin:...), where neither gcloud nor
  # kubectl is. Going through your shell gives the tunnels the PATH of your terminal.
  cat >"$app/Contents/MacOS/tunnels-manager" <<EOF
#!/bin/sh
exec "\${SHELL:-/bin/zsh}" -ilc 'exec "\$0"' "$LAUNCHER" </dev/null
EOF
  chmod +x "$app/Contents/MacOS/tunnels-manager"

  if command -v rsvg-convert >/dev/null; then
    local iconset
    iconset="$(mktemp -d)/icon.iconset"
    mkdir -p "$iconset"
    for size in 16 32 128 256 512; do
      rsvg-convert -w "$size" -h "$size" "$SRC/data/icon.svg" -o "$iconset/icon_${size}x${size}.png"
      rsvg-convert -w $((size * 2)) -h $((size * 2)) "$SRC/data/icon.svg" \
        -o "$iconset/icon_${size}x${size}@2x.png"
    done
    iconutil -c icns "$iconset" -o "$app/Contents/Resources/icon.icns"
    rm -rf "$(dirname "$iconset")"
  else
    echo "    Note: rsvg-convert not found (brew install librsvg), so the app has no icon."
  fi

  note_config
  echo
  echo "Done. Open \"Tunnels Manager\" from Spotlight or ~/Applications, or run: tunnels-manager"
}

if [ "$(uname -s)" = "Darwin" ]; then
  install_macos
  exit 0
fi

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

note_config

echo
echo "Done. Search for \"Tunnels Manager\" in your launcher, or run: tunnels-manager"
