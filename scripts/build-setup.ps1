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

    # Per-user MSI components need HKCU key paths and directory cleanup.
    [xml]$document = Get-Content -LiteralPath $harvest -Raw
    $namespace = $document.DocumentElement.NamespaceURI
    $query = [System.Xml.XmlNamespaceManager]::new($document.NameTable)
    $query.AddNamespace('w', $namespace)
    $directories = @{}
    foreach ($component in $document.SelectNodes('//w:Component', $query)) {
        $id = [string]$component.GetAttribute('Id')
        $identity = [Text.Encoding]::UTF8.GetBytes("Pivluma.Setup.Files/$id")
        $hash = [Security.Cryptography.SHA256]::HashData($identity)
        $component.SetAttribute('Guid', [guid]::new([byte[]]$hash[0..15]).ToString())
        $file = $component.SelectSingleNode('w:File', $query)
        if ($file) { $file.RemoveAttribute('KeyPath') }
        $directory = [string]$component.GetAttribute('Directory')
        if (-not $directories.ContainsKey($directory)) {
            $remove = $document.CreateElement('RemoveFolder', $namespace)
            $remove.SetAttribute('Id', 'Remove_' + $directory)
            $remove.SetAttribute('On', 'uninstall')
            [void]$component.AppendChild($remove)
            $directories[$directory] = $true
        }
        $registry = $document.CreateElement('RegistryValue', $namespace)
        $registry.SetAttribute('Root', 'HKCU')
        $registry.SetAttribute('Key', 'Software\Pivluma\Setup\Files')
        $registry.SetAttribute('Name', $id)
        $registry.SetAttribute('Type', 'integer')
        $registry.SetAttribute('Value', '1')
        $registry.SetAttribute('KeyPath', 'yes')
        [void]$component.AppendChild($registry)
    }
    $rootComponent = $document.SelectSingleNode('//w:Component[@Directory="INSTALLFOLDER"]', $query)
    $extraDirectories = @('LocalPrograms')
    $extraDirectories += $document.SelectNodes('//w:Directory', $query) | ForEach-Object { $_.GetAttribute('Id') }
    foreach ($directory in $extraDirectories) {
        if ($directories.ContainsKey($directory)) { continue }
        $remove = $document.CreateElement('RemoveFolder', $namespace)
        $remove.SetAttribute('Id', 'Remove_' + $directory)
        $remove.SetAttribute('Directory', $directory)
        $remove.SetAttribute('On', 'uninstall')
        [void]$rootComponent.AppendChild($remove)
    }
    $document.Save($harvest)

    $productObject = Join-Path $work 'product.wixobj'
    $filesObject = Join-Path $work 'files.wixobj'
    & (Join-Path $wix 'candle.exe') -nologo -arch x64 "-dVersion=$version" "-dIconPath=$icon" -out $productObject 'scripts/pivluma-setup.wxs'
    if ($LASTEXITCODE -ne 0) { throw 'WiX product compilation failed.' }
    & (Join-Path $wix 'candle.exe') -nologo -arch x64 "-dAppSource=$source" -out $filesObject $harvest
    if ($LASTEXITCODE -ne 0) { throw 'WiX files compilation failed.' }
    $output = "dist/Pivluma-$version-Windows-Setup.msi"
    & (Join-Path $wix 'light.exe') -nologo -sice:ICE91 -out $output $productObject $filesObject
    if ($LASTEXITCODE -ne 0) { throw 'WiX installer linking failed.' }
    Get-FileHash -Algorithm SHA256 $output
} finally {
    Pop-Location
}
