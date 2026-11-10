#!/bin/zsh
# Build Nikon Pill.app and a DMG on macOS.
# Prereqs: python3, pip install pyinstaller; the toolkit is auto-vendored.
set -euo pipefail
cd "$(dirname "$0")"

VENDOR="${NFP_VENDOR_DIR:-$HOME/git/NikonFirmwarePatch-toolkit-v0.4.0-rc.2}"
ZIP_URL='https://github.com/XinmiaoZhao/NikonFirmwarePatch/releases/download/v0.4.0-rc.2/NikonFirmwarePatch-v0.4.0-rc.2-toolkit.zip'
ZIP_SHA='9a00183b54ee80e02e89445a59f5806a1e738ea22e2400538221ca27c21d9b4c'

if [ ! -f "$VENDOR/patch.py" ]; then
  echo "-- vendoring toolkit from Release ZIP (SHA-256 checked) --"
  mkdir -p vendor
  curl -sL -o vendor/nfp-toolkit.zip "$ZIP_URL"
  echo "$ZIP_SHA  vendor/nfp-toolkit.zip" | shasum -a 256 -c -
  unzip -q vendor/nfp-toolkit.zip -d vendor
  VENDOR="$PWD/vendor/NikonFirmwarePatch-v0.4.0-rc.2"
fi
export NFP_VENDOR_DIR="$VENDOR"

# Fenestra engine: only two stdlib files + the pinned fxpatch, all fetched
# over HTTP (no git clone, no repo history).
FENESTRA="${FENESTRA_VENDOR_DIR:-$PWD/vendor/fenestra-z8}"
FEN_RAW='https://raw.githubusercontent.com/PIPA12344/Fenestra-Z8-C311-source/cold-retry3-source'
FX_URL='https://github.com/PIPA12344/Fenestra-Z8-C311-source/releases/download/wlm-2026.11.10-r10/cold-retry3.fxpatch'
FX_SHA='395b05e5db84c464e6cb44b178623cf5d237cd9aef5d8ed9ad0b8cfc244758a1'
if [ ! -f "$FENESTRA/patcher.py" ]; then
  echo "-- vendoring Fenestra engine (2 files, no git) --"
  mkdir -p "$FENESTRA/host"
  curl -sfL -o "$FENESTRA/patcher.py" "$FEN_RAW/patcher.py"
  curl -sfL -o "$FENESTRA/host/cipher_patch.py" "$FEN_RAW/host/cipher_patch.py"
fi
if [ ! -f "$FENESTRA/cold-retry3.fxpatch" ]; then
  echo "-- downloading cold-retry3.fxpatch (SHA-256 checked) --"
  curl -sL -o "$FENESTRA/cold-retry3.fxpatch" "$FX_URL"
  echo "$FX_SHA  $FENESTRA/cold-retry3.fxpatch" | shasum -a 256 -c -
fi
export FENESTRA_VENDOR_DIR="$FENESTRA"

# Homebrew/conda Pythons are externally managed: build in a dedicated venv.
PY="$PWD/.venv-build/bin/python"
if [ ! -x "$PY" ]; then
  python3 -m venv .venv-build
fi
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet pyinstaller PySide6 'capstone==5.0.7'
rm -rf build dist
"$PY" make_icon.py                            # 💊 app icon -> build/icon.icns
"$PY" -m PyInstaller --noconfirm nikon-pill.spec

APP="dist/Nikon Pill.app"
codesign --force --deep -s - "$APP"          # ad-hoc; replaces expired-ctl signing noise

# Version comes from the git tag (v0.1.0 -> 0.1.0); falls back to 0.0.0-dev
# for untagged local builds so the workflow needs no hardcoded numbers.
VERSION=$(git describe --tags --abbrev=0 2>/dev/null | sed 's/^v//' || true)
VERSION=${VERSION:-0.0.0-dev}

VOLNAME="Nikon Pill"
DMG="dist/NikonPill-${VERSION}-macOS.dmg"
rm -f "$DMG"
mkdir -p build/dmg-staging
cp -R "$APP" build/dmg-staging/
ln -sf /Applications build/dmg-staging/Applications
hdiutil create -volname "$VOLNAME" -srcfolder build/dmg-staging -ov -format UDZO "$DMG"
echo "-- done: $DMG --"
