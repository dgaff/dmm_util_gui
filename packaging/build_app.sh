#!/bin/zsh
# Build "DMM Utility.app" into dist/.
# Requires: venv with requirements.txt + pyinstaller installed.
set -e
cd "$(dirname "$0")/.."

QT_QPA_PLATFORM=offscreen venv/bin/python packaging/gen_icon.py

venv/bin/pyinstaller --noconfirm --clean --windowed \
  --name "DMM Utility" \
  --icon "$PWD/packaging/icon.icns" \
  --osx-bundle-identifier com.douggaff.dmm-utility \
  --specpath packaging \
  launcher.py

echo
echo "Built: dist/DMM Utility.app  (drag to /Applications to install)"
