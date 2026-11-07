param([string]$Destination = 'CameraDesk-Source.zip')

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$destinationPath = if ([IO.Path]::IsPathRooted($Destination)) { $Destination } else { Join-Path $projectRoot $Destination }
$staging = Join-Path $projectRoot 'build\source-release\CameraDesk'
$stagingResolved = [IO.Path]::GetFullPath($staging)
$buildRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'build')) + [IO.Path]::DirectorySeparatorChar
if (-not $stagingResolved.StartsWith($buildRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe staging directory.' }
if (Test-Path -LiteralPath $stagingResolved) { Remove-Item -LiteralPath $stagingResolved -Recurse -Force }
New-Item -ItemType Directory -Path $staging -Force | Out-Null
$items = @('.github', 'assets', 'docs', 'LICENSES', 'multicam', 'scripts', 'tests', 'pytest.ini', '.gitignore', 'LICENSE', 'README.md', 'THIRD_PARTY.md', 'CONTRIBUTING.md', 'CHANGELOG.md', 'main.py', 'requirements.txt', 'requirements-dev.txt', 'launch.cmd', 'build.ps1')
foreach ($item in $items) {
    $source = Join-Path $projectRoot $item
    if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination $staging -Recurse -Force }
}
# Only remove generated Python caches within the checked staging directory.
Get-ChildItem -LiteralPath $staging -Directory -Filter '__pycache__' -Recurse | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
Get-ChildItem -LiteralPath $staging -File -Filter '*.pyc' -Recurse | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
Compress-Archive -LiteralPath $staging -DestinationPath $destinationPath -Force
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($destinationPath)
try {
    if (-not ($archive.Entries | Where-Object { $_.FullName.Replace('\', '/') -match '/\.github/workflows/release\.yml$' })) {
        throw 'Source archive is missing the GitHub release workflow.'
    }
} finally { $archive.Dispose() }
Write-Host "Application source: $destinationPath"
