$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    try { py -3.12 -m venv .venv }
    catch { py -3.11 -m venv .venv }
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean GSTRTool.spec

$release = Join-Path $PSScriptRoot "release"
New-Item -ItemType Directory -Force -Path $release | Out-Null
Copy-Item "dist\GSTRTool.exe" "$release\GSTRTool.exe" -Force
Copy-Item "README.md" "$release\README.txt" -Force
Compress-Archive -Path "$release\GSTRTool.exe", "$release\README.txt" -DestinationPath "$release\GSTRTool-Windows.zip" -Force
Write-Host "Release created at $release\GSTRTool-Windows.zip"
