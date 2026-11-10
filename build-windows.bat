@echo off
REM Build Nikon Pill one-file EXE on Windows.
REM Prereqs: Python 3.10+ on PATH; the toolkit is auto-vendored.
setlocal
cd /d "%~dp0"

set "VENDOR=%NFP_VENDOR_DIR%"
if "%VENDOR%"=="" set "VENDOR=%USERPROFILE%\git\NikonFirmwarePatch-toolkit-v0.4.0-rc.2"

set "ZIP_URL=https://github.com/XinmiaoZhao/NikonFirmwarePatch/releases/download/v0.4.0-rc.2/NikonFirmwarePatch-v0.4.0-rc.2-toolkit.zip"
set "ZIP_SHA=9a00183b54ee80e02e89445a59f5806a1e738ea22e2400538221ca27c21d9b4c"

if not exist "%VENDOR%\patch.py" (
  echo -- vendoring toolkit from Release ZIP --
  if not exist vendor mkdir vendor
  curl -sL -o vendor\nfp-toolkit.zip "%ZIP_URL%"
  certutil -hashfile vendor\nfp-toolkit.zip SHA256 | find /i "%ZIP_SHA%" >nul || (echo SHA-256 mismatch & exit /b 1)
  tar -xf vendor\nfp-toolkit.zip -C vendor
  set "VENDOR=%CD%\vendor\NikonFirmwarePatch-v0.4.0-rc.2"
)
set "NFP_VENDOR_DIR=%VENDOR%"

REM Fenestra engine: only two stdlib files + the pinned fxpatch, fetched
REM over HTTP (no git clone).
set "FENESTRA=%FENESTRA_VENDOR_DIR%"
if "%FENESTRA%"=="" set "FENESTRA=%CD%\vendor\fenestra-z8"
set "FEN_RAW=https://raw.githubusercontent.com/PIPA12344/Fenestra-Z8-C311-source/cold-retry3-source"
set "FX_URL=https://github.com/PIPA12344/Fenestra-Z8-C311-source/releases/download/wlm-2026.11.10-r10/cold-retry3.fxpatch"
set "FX_SHA=395b05e5db84c464e6cb44b178623cf5d237cd9aef5d8ed9ad0b8cfc244758a1"

if not exist "%FENESTRA%\patcher.py" (
  echo -- vendoring Fenestra engine -- 2 files, no git
  mkdir "%FENESTRA%\host" 2>nul
  curl -sfL -o "%FENESTRA%\patcher.py" "%FEN_RAW%/patcher.py"
  curl -sfL -o "%FENESTRA%\host\cipher_patch.py" "%FEN_RAW%/host/cipher_patch.py"
)
if not exist "%FENESTRA%\cold-retry3.fxpatch" (
  echo -- downloading cold-retry3.fxpatch --
  curl -sL -o "%FENESTRA%\cold-retry3.fxpatch" "%FX_URL%"
  certutil -hashfile "%FENESTRA%\cold-retry3.fxpatch" SHA256 | find /i "%FX_SHA%" >nul || (echo fxpatch SHA-256 mismatch & exit /b 1)
)
set "FENESTRA_VENDOR_DIR=%FENESTRA%"

python -m pip install --quiet pyinstaller PySide6 capstone==5.0.7
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
python make_icon.py
python -m PyInstaller --noconfirm nikon-pill-win.spec

REM Version comes from the git tag (v0.1.0 -> 0.1.0); falls back to 0.0.0-dev.
set "VERSION="
for /f %%i in ('git describe --tags --abbrev^=0 2^>nul') do set "VERSION=%%i"
set "VERSION=%VERSION:v=%"
if "%VERSION%"=="" set "VERSION=0.0.0-dev"
ren "dist\NikonPill.exe" "NikonPill-%VERSION%-Windows.exe" >nul 2>&1 || copy /y "dist\NikonPill.exe" "dist\NikonPill-%VERSION%-Windows.exe" >nul
echo -- done: dist\NikonPill-%VERSION%-Windows.exe --
endlocal
