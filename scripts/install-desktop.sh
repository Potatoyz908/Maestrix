#!/usr/bin/env bash
# Open Maestri - Linux Desktop Installation Script
set -e

REPO_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/512x512/apps"

mkdir -p "$BIN_DIR" "$APP_DIR" "$ICON_DIR"

echo "📦 Installing Open Maestri to user environment..."

# 1. Symlink / copy launchers to ~/.local/bin
ln -sf "$REPO_DIR/scripts/open-maestri" "$BIN_DIR/open-maestri"
chmod +x "$BIN_DIR/open-maestri"

if [ -f "$REPO_DIR/.build/release/open-maestri-server" ]; then
    ln -sf "$REPO_DIR/.build/release/open-maestri-server" "$BIN_DIR/open-maestri-server"
elif [ -f "$REPO_DIR/.build/debug/open-maestri-server" ]; then
    ln -sf "$REPO_DIR/.build/debug/open-maestri-server" "$BIN_DIR/open-maestri-server"
fi

if [ -f "$REPO_DIR/.build/release/omaestri" ]; then
    ln -sf "$REPO_DIR/.build/release/omaestri" "$BIN_DIR/omaestri"
elif [ -f "$REPO_DIR/.build/debug/omaestri" ]; then
    ln -sf "$REPO_DIR/.build/debug/omaestri" "$BIN_DIR/omaestri"
fi

# 2. Install App Icon
cp "$REPO_DIR/Sources/Assets.xcassets/AppIcon.appiconset/icon_512x512.png" "$ICON_DIR/open-maestri.png"
cp "$REPO_DIR/Sources/Assets.xcassets/AppIcon.appiconset/icon_512x512.png" "$ICON_DIR/org.openmaestri.desktop.native.png"

for size in 16 24 32 48 64 128 256 512 1024; do
    target_dir="$HOME/.local/share/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$target_dir"
    src_icon=""
    if [ -f "$REPO_DIR/Sources/Assets.xcassets/AppIcon.appiconset/icon_${size}x${size}.png" ]; then
        src_icon="$REPO_DIR/Sources/Assets.xcassets/AppIcon.appiconset/icon_${size}x${size}.png"
    elif [ "$size" -eq 1024 ] && [ -f "$REPO_DIR/Sources/Assets.xcassets/AppIcon.appiconset/icon_512x512@2x.png" ]; then
        src_icon="$REPO_DIR/Sources/Assets.xcassets/AppIcon.appiconset/icon_512x512@2x.png"
    fi
    if [ -n "$src_icon" ]; then
        cp "$src_icon" "$target_dir/open-maestri.png"
        cp "$src_icon" "$target_dir/org.openmaestri.desktop.native.png"
    fi
done

# 3. Install .desktop file
# Absolute Exec path: GNOME does not always have ~/.local/bin in the PATH of the session that launches .desktop files
sed "s|@OPEN_MAESTRI_BIN@|$BIN_DIR/open-maestri|g" "$REPO_DIR/scripts/open-maestri.desktop" > "$APP_DIR/open-maestri.desktop"
chmod +x "$APP_DIR/open-maestri.desktop"

if [ -d "$HOME/Desktop" ]; then
    cp "$APP_DIR/open-maestri.desktop" "$HOME/Desktop/open-maestri.desktop"
    chmod +x "$HOME/Desktop/open-maestri.desktop"
    gio set "$HOME/Desktop/open-maestri.desktop" metadata::trusted true 2>/dev/null || true
fi

# 4. Update desktop & icon caches if tools exist
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" 2>/dev/null || true
fi
if command -v xdg-mime >/dev/null 2>&1; then
    xdg-mime default open-maestri.desktop x-scheme-handler/maestri 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

echo "✅ Open Maestri installed successfully!"
echo "   - Command line: open-maestri"
echo "   - CLI tool:     omaestri"
echo "   - Desktop entry: $APP_DIR/open-maestri.desktop"
