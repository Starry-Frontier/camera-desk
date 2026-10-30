param(
    [string]$CacheDirectory = 'third-party-source-cache',
    [string]$Destination = 'CameraDesk-ThirdParty-Sources.zip',
    [string]$BundleDirectory = 'dist\CameraDesk'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$cacheRoot = [IO.Path]::GetFullPath($(if ([IO.Path]::IsPathRooted($CacheDirectory)) { $CacheDirectory } else { Join-Path $projectRoot $CacheDirectory }))
$destinationPath = if ([IO.Path]::IsPathRooted($Destination)) { $Destination } else { Join-Path $projectRoot $Destination }
$manifestPath = Join-Path $PSScriptRoot 'third-party-sources.json'
$sources = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$bundleRoot = if ([IO.Path]::IsPathRooted($BundleDirectory)) { $BundleDirectory } else { Join-Path $projectRoot $BundleDirectory }
$bundle = Join-Path $bundleRoot '_internal'
if (Test-Path -LiteralPath $bundle) {
    $plugins = @(Get-ChildItem -LiteralPath $bundle -Recurse -File -Filter 'opencv_videoio_ffmpeg*.dll')
    foreach ($plugin in $plugins) {
        if ($plugin.Name -ne 'opencv_videoio_ffmpeg4100_64.dll' -or (Get-FileHash -LiteralPath $plugin.FullName -Algorithm MD5).Hash -ne 'bfef029900f788480a363d6dc05c4f0e') {
            throw 'The packaged FFmpeg plugin no longer matches the corresponding-source manifest.'
        }
    }
    $coveredModules = @('Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Network.dll', 'Qt6Widgets.dll', 'Qt6Svg.dll')
    $additionalModules = @(Get-ChildItem -LiteralPath $bundle -Recurse -File -Filter 'Qt6*.dll' | Where-Object { $_.Name -notin $coveredModules })
    if ($additionalModules) { throw "Update the corresponding-source manifest for extra Qt modules: $($additionalModules.Name -join ', ')" }
}
New-Item -ItemType Directory -Path $cacheRoot -Force | Out-Null
$cachePrefix = $cacheRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar

# Downloads stay compressed; no upstream archive is executed or extracted.
foreach ($source in $sources) {
    if ($source.sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw "Invalid checksum: $($source.filename)" }
    $target = [IO.Path]::GetFullPath((Join-Path $cacheRoot $source.filename))
    if (-not $target.StartsWith($cachePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe source archive path.' }
    $valid = (Test-Path -LiteralPath $target) -and ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -eq $source.sha256)
    if (-not $valid) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        $partial = "$target.part"
        Write-Host "Downloading $($source.filename)"
        $downloaded = $false
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            try {
                Invoke-WebRequest -UseBasicParsing -Uri $source.url -OutFile $partial -TimeoutSec 120
                if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $source.sha256) {
                    throw "Source archive checksum mismatch: $($source.filename)"
                }
                Move-Item -LiteralPath $partial -Destination $target -Force
                $downloaded = $true
                break
            } catch {
                if ($attempt -eq 3) { throw }
                Write-Warning "Retrying $($source.filename): $($_.Exception.Message)"
            }
        }
        if (-not $downloaded) { throw "Download failed: $($source.filename)" }
    }
    Write-Host "Verified $($source.filename)"
}

# Assemble only manifest-listed archives and notices, never arbitrary cache files.
$staging = [IO.Path]::GetFullPath((Join-Path $projectRoot 'build\third-party-source-release\CameraDesk-ThirdParty-Sources'))
$buildPrefix = [IO.Path]::GetFullPath((Join-Path $projectRoot 'build')) + [IO.Path]::DirectorySeparatorChar
if (-not $staging.StartsWith($buildPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe source staging directory.' }
if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
New-Item -ItemType Directory -Path $staging -Force | Out-Null
foreach ($source in $sources) {
    $destinationFile = Join-Path $staging $source.filename
    New-Item -ItemType Directory -Path (Split-Path -Parent $destinationFile) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $cacheRoot $source.filename) -Destination $destinationFile
}
Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $staging 'manifest.json')
Copy-Item -LiteralPath (Join-Path $projectRoot 'docs\THIRD_PARTY_SOURCES.md') -Destination (Join-Path $staging 'SOURCES.md')
Copy-Item -LiteralPath (Join-Path $projectRoot 'LICENSES') -Destination $staging -Recurse
$hashes = foreach ($source in $sources) { '{0}  {1}' -f $source.sha256.ToLowerInvariant(), $source.filename }
$hashes | Set-Content -LiteralPath (Join-Path $staging 'SHA256SUMS.txt') -Encoding utf8
Compress-Archive -LiteralPath $staging -DestinationPath $destinationPath -Force
Write-Host "Corresponding third-party sources: $destinationPath"
