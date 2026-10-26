#!/bin/bash
# PostQueue builder for macOS. Run on a Mac: ./build_macos.sh
# The build targets the CPU architecture of the Python used (arm64 on Apple silicon, x86_64 on Intel).
#   PY=/path/to/python3                      build with a specific Python (default: python3)
#   SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"   sign the app and the disk image
#   NOTARY_PROFILE=<notarytool keychain profile>                   also notarize and staple (needs SIGN_IDENTITY)
# Outputs in dist/ (NAME = PostQueue-<version>-macos-<arch>):
#   dist/PostQueue.app   the application      dist/NAME.dmg   disk image: drag PostQueue to Applications   dist/NAME.zip   the app, zipped
set -euo pipefail
cd "$(dirname "$0")"
PY="${PY:-python3}"
PYI_PINNED=6.11.1
if [ "$(uname)" != "Darwin" ]; then echo "This script builds the macOS app and must run on a Mac."; exit 1; fi
"$PY" -m PyInstaller --version >/dev/null 2>&1 || { echo "Build tools missing. Install them with: $PY -m pip install -r requirements-build.txt"; exit 1; }
ARCH="$("$PY" -c 'import platform; print(platform.machine())')"
VERSION="$("$PY" -c 'from version import VERSION; print(VERSION)')"
PYI_FOUND="$("$PY" -m PyInstaller --version)"
NAME="PostQueue-$VERSION-macos-$ARCH"
echo "PostQueue $VERSION - building macOS $ARCH"
[ "$PYI_FOUND" = "$PYI_PINNED" ] || echo "WARNING: PyInstaller $PYI_FOUND found; builds are only reproducible with $PYI_PINNED from requirements-build.txt."

if "$PY" -m pytest --version >/dev/null 2>&1; then
  QT_QPA_PLATFORM=offscreen "$PY" -m pytest -q -p no:cacheprovider tests || { echo "Tests failed - build aborted."; exit 1; }
else
  echo "WARNING: pytest not installed - skipping tests. Install requirements-build.txt to enable them."
fi

[ -f assets/app.icns ] || { echo "assets/app.icns missing. Run: $PY make_assets.py"; exit 1; }
rm -rf dist/PostQueue.app "dist/$NAME.dmg" "dist/$NAME.zip" build/macos
# What goes into the package, and which unused Qt parts are filtered out, is defined in installer/PostQueue.spec.
PQ_NAME="$NAME" PQ_MODE=app "$PY" -m PyInstaller --noconfirm --clean --workpath build/macos --distpath dist installer/PostQueue.spec
rm -rf "dist/PostQueue"   # the bare one-folder build; the .app bundle is what ships
cp THIRD_PARTY_NOTICES.txt dist/THIRD_PARTY_NOTICES.txt; rm -rf dist/licenses; cp -R licenses dist/licenses

if [ -n "${SIGN_IDENTITY:-}" ]; then
  codesign --deep --force --options runtime --timestamp --sign "$SIGN_IDENTITY" dist/PostQueue.app
  codesign --verify --deep --strict dist/PostQueue.app
else
  echo "NOTE: Not signed - Gatekeeper will warn on first launch (right-click > Open). See BUILD_AND_TEST_GUIDE.md, section macOS."
fi

# Disk image with an Applications shortcut: the user drags the app across, one gesture.
STAGE="$(mktemp -d)"; cp -R dist/PostQueue.app "$STAGE/"; ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "PostQueue" -srcfolder "$STAGE" -ov -format UDZO "dist/$NAME.dmg" >/dev/null
rm -rf "$STAGE"
ditto -c -k --keepParent dist/PostQueue.app "dist/$NAME.zip"

if [ -n "${SIGN_IDENTITY:-}" ]; then
  codesign --force --timestamp --sign "$SIGN_IDENTITY" "dist/$NAME.dmg"
  if [ -n "${NOTARY_PROFILE:-}" ]; then
    xcrun notarytool submit "dist/$NAME.dmg" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "dist/$NAME.dmg"; xcrun stapler staple dist/PostQueue.app
  fi
fi
for f in "dist/$NAME.dmg" "dist/$NAME.zip"; do shasum -a 256 "$f" > "$f.sha256.txt"; done
echo "Output: dist/PostQueue.app, dist/$NAME.dmg, dist/$NAME.zip"
