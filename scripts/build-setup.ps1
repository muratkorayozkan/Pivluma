param(
    [string]$Python = 'python',
    [string]$WixBin = '.venv/wix'
)

$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..')
try {
    $wix = (Resolve-Path $WixBin).Path
    $source = (Resolve-Path 'dist/Pivluma').Path
    $icon = (Resolve-Path 'pivluma/pivluma.ico').Path
    $version = & $Python -c 'from pivluma import __version__; print(__version__)'
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
        throw 'Could not determine the Pivluma version.'
    }
    $work = (New-Item -Path 'build/setup' -ItemType Directory -Force).FullName
    $harvest = Join-Path $work 'files.wxs'
    & (Join-Path $wix 'heat.exe') dir $source -nologo -ag -srd -sreg -scom -dr INSTALLFOLDER -cg ApplicationFiles -var var.AppSource -out $harvest
    if ($LASTEXITCODE -ne 0) { throw 'WiX file harvesting failed.' }

    $banner = (Resolve-Path 'scripts/installer/banner.bmp').Path
    $dialog = (Resolve-Path 'scripts/installer/dialog.bmp').Path
    $ui = Join-Path $wix 'WixUIExtension.dll'

    $productObject = Join-Path $work 'product.wixobj'
    $filesObject = Join-Path $work 'files.wixobj'
    & (Join-Path $wix 'candle.exe') -nologo -arch x64 "-dVersion=$version" "-dIconPath=$icon" "-dBannerPath=$banner" "-dDialogPath=$dialog" -out $productObject 'scripts/pivluma-setup.wxs'
    if ($LASTEXITCODE -ne 0) { throw 'WiX product compilation failed.' }
    & (Join-Path $wix 'candle.exe') -nologo -arch x64 "-dAppSource=$source" -out $filesObject $harvest
    if ($LASTEXITCODE -ne 0) { throw 'WiX files compilation failed.' }
    $output = "dist/Pivluma-$version-Windows-Setup.msi"
    & (Join-Path $wix 'light.exe') -nologo -ext $ui -cultures:en-us -out $output $productObject $filesObject
    if ($LASTEXITCODE -ne 0) { throw 'WiX installer linking failed.' }
    Get-FileHash -Algorithm SHA256 $output
} finally {
    Pop-Location
}
