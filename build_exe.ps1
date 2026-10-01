# Build a single-file Windows executable: dist\AI-Control-Room.exe
# Usage (from the repository folder):  powershell -ExecutionPolicy Bypass -File build_exe.ps1
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$python = '.\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    py -3 -m venv .venv
}
& $python -m pip install --quiet -r requirements.txt 'pyinstaller>=6.16,<7'
if ($LASTEXITCODE -ne 0) { throw 'Dependency install failed.' }

# Application icon cropped from the chamber artwork (build output only, not committed).
New-Item -ItemType Directory -Force build | Out-Null
& $python -c @'
from PIL import Image
im = Image.open('assets/room/council-chamber-v1.png').convert('RGB')
w, h = im.size
side = min(w, h) * 6 // 10
left, top = (w - side) // 2, (h - side) // 4
im.crop((left, top, left + side, top + side)).save(
    'build/app.ico', sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
'@
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed.' }

& $python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name 'AI-Control-Room' `
    --icon 'build\app.ico' `
    --add-data 'assets;assets' `
    --add-data 'config.example.json;.' `
    main.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

$exe = Get-Item 'dist\AI-Control-Room.exe'
'Built {0} ({1:N1} MB)' -f $exe.FullName, ($exe.Length / 1MB)
