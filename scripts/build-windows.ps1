param([string]$Python = 'python')

$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..')
try {
    $icon = (Resolve-Path 'pivluma/pivluma.ico').Path
    $trustFile = & $Python -c 'import ssl; from pathlib import Path; print(Path(ssl.__file__).with_name("codex-rejected-cas.pem"))'
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the Python TLS runtime.' }
    $runtimeData = @()
    if (Test-Path -LiteralPath $trustFile -PathType Leaf) {
        $runtimeData = @('--add-data', "${trustFile}:.")
    }
    & $Python -m PyInstaller --noconfirm --clean --windowed --onedir --name Pivluma `
        --paths . --icon $icon --add-data "${icon}:pivluma" @runtimeData --specpath build --workpath build/pyinstaller --distpath dist scripts/windows_launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }

    $exe = (Resolve-Path 'dist/Pivluma/Pivluma.exe').Path
    $previousPlatform = [Environment]::GetEnvironmentVariable('QT_QPA_PLATFORM', 'Process')
    try {
        $env:QT_QPA_PLATFORM = 'offscreen'
        $process = Start-Process -FilePath $exe -ArgumentList '--smoke-test' -PassThru -WindowStyle Hidden
        if (-not $process.WaitForExit(30000)) {
            $process.Kill()
            throw 'Packaged app smoke test timed out.'
        }
        if ($process.ExitCode -ne 0) { throw "Packaged app smoke test failed ($($process.ExitCode))." }
    } finally {
        [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM', $previousPlatform, 'Process')
    }

    $version = & $Python -c 'from pivluma import __version__; print(__version__)'
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
        throw 'Could not determine the Pivluma version.'
    }
    $archive = "dist/Pivluma-$version-Windows.zip"
    Compress-Archive -Path 'dist/Pivluma' -DestinationPath $archive -Force
    Get-FileHash -Algorithm SHA256 $archive
} finally {
    Pop-Location
}
