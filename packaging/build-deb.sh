#!/bin/bash
# Rebuilds the scp-gui .deb package from the current source tree.
# Run from the repo root:  ./packaging/build-deb.sh
set -e

VERSION=$(grep -m1 '^Version:' packaging/debian/control | awk '{print $2}')
PKG_ROOT="build/scp-gui_${VERSION}_all"

echo "Building scp-gui ${VERSION}..."

rm -rf build
mkdir -p "$PKG_ROOT/DEBIAN"
mkdir -p "$PKG_ROOT/usr/lib/python3/dist-packages"
mkdir -p "$PKG_ROOT/usr/bin"
mkdir -p "$PKG_ROOT/usr/share/applications"
mkdir -p "$PKG_ROOT/usr/share/doc/scp-gui"
mkdir -p "$PKG_ROOT/usr/share/pixmaps"
mkdir -p "$PKG_ROOT/usr/share/icons/hicolor/scalable/apps"

cp -r scpgui "$PKG_ROOT/usr/lib/python3/dist-packages/"
rm -rf "$PKG_ROOT/usr/lib/python3/dist-packages/scpgui/__pycache__"

cat > "$PKG_ROOT/usr/bin/scp-gui" <<'EOF'
#!/bin/sh
exec python3 -m scpgui.main "$@"
EOF
chmod 755 "$PKG_ROOT/usr/bin/scp-gui"

cp packaging/debian/control "$PKG_ROOT/DEBIAN/control"
cp packaging/debian/postinst "$PKG_ROOT/DEBIAN/postinst"
chmod 755 "$PKG_ROOT/DEBIAN/postinst"
cp packaging/debian/scp-gui.desktop "$PKG_ROOT/usr/share/applications/scp-gui.desktop"
cp packaging/debian/copyright "$PKG_ROOT/usr/share/doc/scp-gui/copyright"
cp logo.png "$PKG_ROOT/usr/share/pixmaps/scp-gui.png"
cp packaging/debian/icons/scp-gui.svg "$PKG_ROOT/usr/share/icons/hicolor/scalable/apps/scp-gui.svg"

find "$PKG_ROOT" -type d -exec chmod 755 {} \;
find "$PKG_ROOT" -type f -exec chmod 644 {} \;
chmod 755 "$PKG_ROOT/usr/bin/scp-gui"
chmod 755 "$PKG_ROOT/DEBIAN/postinst"

dpkg-deb --build --root-owner-group "$PKG_ROOT"

echo "Done: build/scp-gui_${VERSION}_all.deb"
