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

VOLNAME="Nikon Pill"
DMG="dist/NikonPill-0.1.0-macOS.dmg"
rm -f "$DMG"
mkdir -p build/dmg-staging
cp -R "$APP" build/dmg-staging/
ln -sf /Applications build/dmg-staging/Applications
hdiutil create -volname "$VOLNAME" -srcfolder build/dmg-staging -ov -format UDZO "$DMG"
echo "-- done: $DMG --"
