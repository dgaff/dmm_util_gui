# Build "DMM Utility.exe" into dist\ on Windows (64-bit).
#
# Run from a 64-bit x86 Python so the produced .exe is x86-64 — it then runs
# natively on x64 Windows AND under emulation on ARM64 Windows, covering both
# with one binary. (PyInstaller is not a cross-compiler: build on Windows.)
#
# Prerequisites (once):
#   py -3 -m venv winvenv
#   winvenv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
#
# Then:
#   powershell -ExecutionPolicy Bypass -File packaging\build_app.ps1

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# Leading .\ is required: a bare relative path passed to & is treated as a
# command/module name, not a file path.
$py = ".\winvenv\Scripts\python.exe"
$pyinstaller = ".\winvenv\Scripts\pyinstaller.exe"

# Render the Windows .ico (offscreen so it needs no display).
$env:QT_QPA_PLATFORM = "offscreen"
& $py packaging\gen_icon.py
Remove-Item Env:\QT_QPA_PLATFORM

# --windowed: no console window for the GUI.
# --onefile:  a single distributable .exe (extracts to a temp dir at launch).
# --collect-all bleak: pull in bleak's WinRT backend, which is imported
#   dynamically and is otherwise easy for PyInstaller to miss.
& $pyinstaller --noconfirm --clean `
    --onefile --windowed `
    --name "DMM Utility" `
    --icon "packaging\icon.ico" `
    --collect-all bleak `
    --collect-all winrt `
    launcher.py

Write-Host ""
Write-Host "Built: dist\DMM Utility.exe"
