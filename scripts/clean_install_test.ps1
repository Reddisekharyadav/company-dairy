param(
    [switch]$SkipBuild,
    [string]$Exe = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    if (-not $SkipBuild) {
        Write-Host "Building one-file executable..."
        & ".\venv\Scripts\python.exe" -m PyInstaller worksense_onefile.spec --noconfirm --clean
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
    }

    if (-not (Test-Path ".\dist\WorkSense.exe")) {
        throw "dist\WorkSense.exe is missing. Run without -SkipBuild first."
    }

    if ($Exe) {
        $resolved = Resolve-Path $Exe
        $expected = Join-Path $ProjectRoot "dist\WorkSense.exe"
        if ($resolved.Path -ne (Resolve-Path $expected).Path) {
            throw "The clean-install test uses dist\WorkSense.exe; pass -Exe only for that file."
        }
    }

    Write-Host "Running isolated packaged smoke test (temporary APPDATA and database)..."
    & ".\venv\Scripts\python.exe" ".\scripts\test_packaged.py"
    if ($LASTEXITCODE -ne 0) { throw "Packaged smoke test failed with exit code $LASTEXITCODE" }

    Write-Host "Clean-install test passed. Results are in .\test-artifacts\packaged-*.json"
}
finally {
    Pop-Location
}
