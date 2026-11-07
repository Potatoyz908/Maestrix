#!/usr/bin/env bash
# Maestrix - Debian/Ubuntu (.deb) Package Builder
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
VERSION="1.0.0"
ARCH="amd64"
PKG_NAME="maestrix"
BUILD_ROOT="/tmp/maestrix-deb-build"
OUTPUT_DIR="$REPO_DIR/releases"

echo "🔨 Building Maestrix .deb package v${VERSION}..."

# 1. Clean previous build directory
rm -rf "$BUILD_ROOT"
mkdir -p "$BUILD_ROOT/DEBIAN"
mkdir -p "$BUILD_ROOT/usr/bin"
mkdir -p "$BUILD_ROOT/usr/lib/maestrix/bin"
mkdir -p "$BUILD_ROOT/usr/lib/maestrix/scripts"
mkdir -p "$BUILD_ROOT/usr/lib/maestrix/lib/swift/linux"
mkdir -p "$BUILD_ROOT/usr/lib/maestrix/lib/girepository-1.0"
mkdir -p "$BUILD_ROOT/usr/share/applications"
mkdir -p "$BUILD_ROOT/usr/share/icons/hicolor"
mkdir -p "$OUTPUT_DIR"

# 2. Compile release binaries if needed
if [ ! -f "$REPO_DIR/.build/release/open-maestri-server" ] || [ ! -f "$REPO_DIR/.build/release/omaestri" ]; then
    echo "⚡ Compiling Swift release binaries..."
    swift build -c release -Xswiftc -use-ld=lld
fi

# Copy release binaries
cp "$REPO_DIR/.build/release/open-maestri-server" "$BUILD_ROOT/usr/lib/maestrix/bin/"
cp "$REPO_DIR/.build/release/omaestri" "$BUILD_ROOT/usr/lib/maestrix/bin/"
chmod +x "$BUILD_ROOT/usr/lib/maestrix/bin/"*

# 3. Copy Swift runtime libraries for standalone execution
SWIFT_LIB_DIR="${SWIFT_LIB_DIR:-$HOME/.local/share/swift-6.0.3/usr/lib/swift/linux}"
if [ -d "$SWIFT_LIB_DIR" ]; then
    echo "📦 Bundling Swift runtime libraries from $SWIFT_LIB_DIR..."
    SWIFT_LIBS=(
        "libswiftCore.so"
        "libswift_Concurrency.so"
        "libswift_StringProcessing.so"
        "libswift_RegexParser.so"
        "libswiftGlibc.so"
        "libBlocksRuntime.so"
        "libdispatch.so"
        "libswiftDispatch.so"
        "libFoundation.so"
        "libFoundationEssentials.so"
        "libFoundationInternationalization.so"
        "libFoundationNetworking.so"
        "lib_FoundationICU.so"
        "libswiftSynchronization.so"
    )
    for lib in "${SWIFT_LIBS[@]}"; do
        if [ -f "$SWIFT_LIB_DIR/$lib" ]; then
            cp -L "$SWIFT_LIB_DIR/$lib" "$BUILD_ROOT/usr/lib/maestrix/lib/swift/linux/"
        fi
    done
fi

# 4. Copy GI typelibs if available
LOCAL_GI_DIR="$HOME/.local/lib/girepository-1.0"
if [ -d "$LOCAL_GI_DIR" ]; then
    echo "📦 Bundling GObject Introspection typelibs..."
    cp -r "$LOCAL_GI_DIR/"* "$BUILD_ROOT/usr/lib/maestrix/lib/girepository-1.0/" 2>/dev/null || true
fi

# 5. Copy Python app sources
echo "📦 Copying application modules..."
cp -r "$REPO_DIR/gtk4-ui" "$BUILD_ROOT/usr/lib/maestrix/"
cp -r "$REPO_DIR/canvas-cairo" "$BUILD_ROOT/usr/lib/maestrix/"
cp -r "$REPO_DIR/maestri_core" "$BUILD_ROOT/usr/lib/maestrix/"
cp "$REPO_DIR/scripts/maestrix-gtk.css" "$BUILD_ROOT/usr/lib/maestrix/scripts/"

# Clean Python caches from package
find "$BUILD_ROOT/usr/lib/maestrix" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_ROOT/usr/lib/maestrix" -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_ROOT/usr/lib/maestrix" -type f -name "*.pyc" -delete 2>/dev/null || true

# 6. Create production runner script
cat <<'EOF' > "$BUILD_ROOT/usr/lib/maestrix/scripts/maestrix-app"
#!/usr/bin/env bash
set -e

APP_DIR="/usr/lib/maestrix"
export GI_TYPELIB_PATH="$APP_DIR/lib/girepository-1.0:$HOME/.local/lib/girepository-1.0:${GI_TYPELIB_PATH:-}"
export LD_LIBRARY_PATH="$APP_DIR/lib/swift/linux:${LD_LIBRARY_PATH:-}"
export PYTHONPATH="$APP_DIR/canvas-cairo:$APP_DIR/canvas-cairo/src:$APP_DIR:$PYTHONPATH"

DATA_DIR="${MAESTRIX_DATA_DIR:-${OPEN_MAESTRI_DATA_DIR:-$HOME/.open-maestri}}"
export OPEN_MAESTRI_DATA_DIR="$DATA_DIR"
RUN_DIR="$DATA_DIR/run"
mkdir -p "$RUN_DIR"
SOCK_PATH="$RUN_DIR/agent.sock"
SERVER_BIN="$APP_DIR/bin/open-maestri-server"
export OPEN_MAESTRI_SERVER_BIN="$SERVER_BIN"

server_alive() {
    [ -S "$SOCK_PATH" ] || return 1
    python3 - "$SOCK_PATH" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket(socket.AF_UNIX)
s.settimeout(1.0)
try:
    s.connect(sys.argv[1])
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}

if server_alive && [ ! -s "$RUN_DIR/server.port" ]; then
    OLD_PID="$(ss -xlpn 2>/dev/null | grep -F "$SOCK_PATH" | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)"
    if [ -n "$OLD_PID" ] && [ "$(cat /proc/$OLD_PID/comm 2>/dev/null)" = "open-maestri-se" ]; then
        kill "$OLD_PID" 2>/dev/null || true
        rm -f "$SOCK_PATH"
    fi
fi

if [ -f "$SERVER_BIN" ] && ! server_alive; then
    rm -f "$SOCK_PATH"
    nohup "$SERVER_BIN" > "$RUN_DIR/server.log" 2>&1 &
    for i in $(seq 1 50); do
        if server_alive; then break; fi
        sleep 0.1
    done
fi

exec python3 "$APP_DIR/gtk4-ui/src/app.py" "$@"
EOF
chmod +x "$BUILD_ROOT/usr/lib/maestrix/scripts/maestrix-app"

# Main entry symlink in /usr/bin
cat <<'EOF' > "$BUILD_ROOT/usr/bin/maestrix"
#!/usr/bin/env bash
exec /usr/lib/maestrix/scripts/maestrix-app "$@"
EOF
chmod +x "$BUILD_ROOT/usr/bin/maestrix"

# CLI wrapper in /usr/bin
cat <<'EOF' > "$BUILD_ROOT/usr/bin/maestrix-cli"
#!/usr/bin/env bash
export LD_LIBRARY_PATH="/usr/lib/maestrix/lib/swift/linux:${LD_LIBRARY_PATH:-}"
exec /usr/lib/maestrix/bin/omaestri "$@"
EOF
chmod +x "$BUILD_ROOT/usr/bin/maestrix-cli"

# Server wrapper in /usr/bin
cat <<'EOF' > "$BUILD_ROOT/usr/bin/maestrix-server"
#!/usr/bin/env bash
export LD_LIBRARY_PATH="/usr/lib/maestrix/lib/swift/linux:${LD_LIBRARY_PATH:-}"
exec /usr/lib/maestrix/bin/open-maestri-server "$@"
EOF
chmod +x "$BUILD_ROOT/usr/bin/maestrix-server"

# Aliases for backwards compatibility
ln -sf maestrix "$BUILD_ROOT/usr/bin/open-maestri"
ln -sf maestrix-cli "$BUILD_ROOT/usr/bin/omaestri"

# 7. Desktop Entry and Icons
cat <<'EOF' > "$BUILD_ROOT/usr/share/applications/maestrix.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=Maestrix
GenericName=Spatial AI Canvas
Comment=Spatial AI Canvas for Autonomous Agents
Exec=/usr/bin/maestrix %U
Icon=maestrix
Terminal=false
Categories=Development;IDE;
StartupWMClass=org.maestrix.desktop
Keywords=AI;Canvas;Terminal;Agent;Maestrix;Maestri;
MimeType=x-scheme-handler/maestri;
StartupNotify=true
EOF

for size in 16 24 32 48 64 128 256 512; do
    target_dir="$BUILD_ROOT/usr/share/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$target_dir"
    src_icon="$REPO_DIR/Sources/Assets.xcassets/AppIcon.appiconset/icon_${size}x${size}.png"
    if [ -f "$src_icon" ]; then
        cp "$src_icon" "$target_dir/maestrix.png"
        cp "$src_icon" "$target_dir/org.maestrix.desktop.png"
    fi
done

# 8. Debian Package Control File
INSTALLED_SIZE=$(du -sk "$BUILD_ROOT" | cut -f1)

cat <<EOF > "$BUILD_ROOT/DEBIAN/control"
Package: ${PKG_NAME}
Version: ${VERSION}
Section: devel
Priority: optional
Architecture: ${ARCH}
Installed-Size: ${INSTALLED_SIZE}
Maintainer: Maestrix Team <euller2005@gmail.com>
Homepage: https://github.com/Potatoyz908/Maestrix
Depends: python3 (>= 3.10), python3-gi, python3-cairo, gir1.2-gtk-4.0, libc6 (>= 2.34), libstdc++6, libcurl4
Recommends: gir1.2-vte-3.91, gir1.2-webkit-6.0
Description: Spatial AI Canvas for Autonomous Agents (Linux Ubuntu)
 Maestrix is the high-performance Linux port of Open Maestri.
 It provides an infinite spatial canvas to run, visualize, and coordinate
 multiple autonomous AI coding agents (Claude Code, Gemini CLI, Codex CLI,
 Aider, OpenCode) with VTE PTY terminals, Verlet physics cable connections,
 Markdown sticky notes, and WebKit portals.
EOF

# postinst script
cat <<'EOF' > "$BUILD_ROOT/DEBIAN/postinst"
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor || true
fi
exit 0
EOF
chmod 755 "$BUILD_ROOT/DEBIAN/postinst"

# postrm script
cat <<'EOF' > "$BUILD_ROOT/DEBIAN/postrm"
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor || true
fi
exit 0
EOF
chmod 755 "$BUILD_ROOT/DEBIAN/postrm"

# 9. Build the debian package
DEB_FILE="$OUTPUT_DIR/${PKG_NAME}_${VERSION}_${ARCH}.deb"
dpkg-deb --build --root-owner-group "$BUILD_ROOT" "$DEB_FILE"

echo "🎉 Package built successfully: $DEB_FILE"
ls -lh "$DEB_FILE"

