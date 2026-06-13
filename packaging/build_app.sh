#!/bin/zsh
# Build "DMM Utility.app" into dist/.
# Requires: venv with requirements.txt + pyinstaller installed.
set -e
cd "$(dirname "$0")/.."

QT_QPA_PLATFORM=offscreen venv/bin/python packaging/gen_icon.py

# Build from the .spec (not launcher.py) so the bundle's Info.plist additions
# — e.g. NSBluetoothAlwaysUsageDescription — are preserved.
venv/bin/pyinstaller --noconfirm --clean "packaging/DMM Utility.spec"

echo
echo "Built: dist/DMM Utility.app  (drag to /Applications to install)"
