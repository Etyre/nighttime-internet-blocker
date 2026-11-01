#!/bin/bash
# Builds build/Night Block.app (menu bar), build/Night Block Override.app (Dock), build/nightblock.icns. Needs Xcode.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; OUT="$HERE/../build"; rm -rf "$OUT"; mkdir -p "$OUT"
cd "$OUT"
swift "$HERE/make_icon.swift" nightblock.iconset
iconutil -c icns nightblock.iconset -o nightblock.icns

APP="$OUT/Night Block.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O -o "$APP/Contents/MacOS/NightBlockMenu" "$HERE/NightBlockMenu.swift" -framework Cocoa 2>&1 | grep -v '^$' || true
[ -x "$APP/Contents/MacOS/NightBlockMenu" ]
cp nightblock.icns "$APP/Contents/Resources/AppIcon.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Night Block</string>
  <key>CFBundleDisplayName</key><string>Night Block</string>
  <key>CFBundleIdentifier</key><string>com.elityre.nightblock.menu</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleExecutable</key><string>NightBlockMenu</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
codesign --force --sign - "$APP" 2>/dev/null || true

OVR="$OUT/Night Block Override.app"
osacompile -o "$OVR" "$HERE/override.applescript"
cp nightblock.icns "$OVR/Contents/Resources/applet.icns"
rm -f "$OVR/Contents/Resources/Assets.car"   # otherwise the applet shows the generic script icon
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.elityre.nightblock.override" "$OVR/Contents/Info.plist" 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string com.elityre.nightblock.override" "$OVR/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleName Night Block Override" "$OVR/Contents/Info.plist" 2>/dev/null || true
codesign --force --sign - "$OVR" 2>/dev/null || true
rm -rf nightblock.iconset
echo "built: $APP"; echo "built: $OVR"
