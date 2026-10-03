# Run in this repository without installing packages. Forward every CLI argument.
$ifsPython = Get-Command python -ErrorAction SilentlyContinue
$ifsLauncher = Get-Command py -ErrorAction SilentlyContinue
$ifsBundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
Push-Location $PSScriptRoot
try {
    if (Test-Path -LiteralPath $ifsBundled) { & $ifsBundled (Join-Path $PSScriptRoot 'ifs.py') @args }
    elseif ($ifsLauncher) { & $ifsLauncher.Source -3 (Join-Path $PSScriptRoot 'ifs.py') @args }
    elseif ($ifsPython) { & $ifsPython.Source (Join-Path $PSScriptRoot 'ifs.py') @args }
    else { throw 'Python 3.10 or newer is required.' }
    $ifsExit = $LASTEXITCODE
} finally { Pop-Location }
exit $ifsExit
