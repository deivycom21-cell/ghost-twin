# ghost-twin quick installer for Windows (PowerShell 5.1+).
#   irm https://raw.githubusercontent.com/deivycom21-cell/ghost-twin/main/scripts/install.ps1 | iex
# Optional: $env:GHOST_TWIN_REF = "main"
$ErrorActionPreference = "Stop"

$Repo = "https://github.com/deivycom21-cell/ghost-twin.git"
$Ref = if ($env:GHOST_TWIN_REF) { $env:GHOST_TWIN_REF } else { "main" }
$Spec = "git+$Repo@$Ref"

function Say($msg) { Write-Host "[ghost-twin] $msg" -ForegroundColor Magenta }
function Die($msg) { Write-Host "[ghost-twin] $msg" -ForegroundColor Red; exit 1 }

# Prefer the py launcher, fall back to python on PATH (skip the Microsoft Store stub).
$Py = $null
if (Get-Command py -ErrorAction SilentlyContinue) { $Py = @("py", "-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $Py = @("python") }
if (-not $Py) { Die "Python 3.10+ is required: winget install Python.Python.3.12" }

$ver = & $Py[0] $Py[1..9] -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$ver -lt [version]"3.10") { Die "Python 3.10+ is required, found $ver" }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Die "git is required: winget install Git.Git" }

if (Get-Command pipx -ErrorAction SilentlyContinue) {
    Say "Installing with pipx..."
    pipx install --force $Spec
    $GtPy = Join-Path (pipx environment --value PIPX_LOCAL_VENVS) "ghost-twin\Scripts\python.exe"
} else {
    Say "Installing with pip (user site)..."
    & $Py[0] $Py[1..9] -m pip install --user --upgrade --quiet $Spec
    if ($LASTEXITCODE -ne 0) { Die "pip install failed" }
    $GtPy = (& $Py[0] $Py[1..9] -c "import sys; print(sys.executable)")
    $Scripts = (& $Py[0] $Py[1..9] -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))")
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($UserPath -notlike "*$Scripts*") {
        [Environment]::SetEnvironmentVariable("Path", "$UserPath;$Scripts", "User")
        $env:Path = "$env:Path;$Scripts"
        Say "Added $Scripts to your user PATH (open a new terminal to pick it up)."
    }
}

Say "Downloading headless Chromium (~150 MB, once)..."
& $GtPy -m playwright install chromium
if ($LASTEXITCODE -ne 0) { Die "playwright install chromium failed" }

Say "Done! Try:"
Write-Host "    ghost-twin doctor"
Write-Host "    ghost-twin demo          # buggy demo app on :3000 (in another terminal)"
Write-Host "    ghost-twin attack http://localhost:3000 --users 4 --duration 60s --dashboard"
