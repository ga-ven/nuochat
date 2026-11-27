$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (Test-Path ".venv\Scripts\python.exe") {
    $python = ".venv\Scripts\python.exe"
} else {
    $python = "python"
}

& $python -m PyInstaller --noconfirm --clean NuoChat.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed"
}

$exe = Join-Path $PSScriptRoot "dist\NuoChat.exe"
if (-not (Test-Path $exe)) {
    throw "Build output not found: $exe"
}

$hash = Get-FileHash $exe -Algorithm SHA256
Write-Output "Built: $exe"
Write-Output "SHA256: $($hash.Hash)"
