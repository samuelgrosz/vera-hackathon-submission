# Installs Vera on Windows (PowerShell).
#   irm https://samuelgrosz.github.io/vera-hackathon-submission/install.ps1 | iex
#
# Optional settings (environment variables, set before running):
#   $env:VERA_HOME = 'C:\some\folder'   where to put Vera (default: %USERPROFILE%\vera-cli)
#   $env:VERA_REPO = '<git url>'        use a different repository
#   $env:VERA_NO_PATH = '1'             do not change your PATH
$ErrorActionPreference = 'Stop'

$Repo = if ($env:VERA_REPO) { $env:VERA_REPO } else { 'https://github.com/samuelgrosz/vera-hackathon-submission.git' }
$Dest = if ($env:VERA_HOME) { $env:VERA_HOME } else { Join-Path $env:USERPROFILE 'vera-cli' }

function Fail([string]$Message) {
    Write-Host "Error: $Message" -ForegroundColor Red
    throw $Message
}

function Test-Python([string]$Exe, [string[]]$Extra) {
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $false }
    try {
        & $Exe @Extra -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail 'Git is not installed. Get it from https://git-scm.com/downloads, then run this again.'
}

$HasPython = (Test-Python 'python' @()) -or (Test-Python 'py' @('-3')) -or (Test-Python 'python3' @())
if (-not $HasPython) {
    Fail 'Python 3.10 or newer is required. Get it from https://www.python.org/downloads/, then run this again.'
}

if (Test-Path (Join-Path $Dest '.git')) {
    Write-Host "Updating Vera in $Dest"
    git -C $Dest pull --ff-only
} elseif ((Test-Path $Dest) -and (Get-ChildItem $Dest -Force | Select-Object -First 1)) {
    Fail "$Dest already exists and is not a Vera install. Set `$env:VERA_HOME to another folder and run this again."
} else {
    Write-Host "Downloading Vera into $Dest"
    git clone --depth 1 $Repo $Dest
}
if ($LASTEXITCODE -ne 0) {
    Fail 'Git could not download Vera. Check your connection and try again.'
}

if (-not $env:VERA_NO_PATH) {
    $UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $Parts = @()
    if ($UserPath) { $Parts = @($UserPath -split ';' | Where-Object { $_ }) }
    if ($Parts -notcontains $Dest) {
        [Environment]::SetEnvironmentVariable('Path', (($Parts + $Dest) -join ';'), 'User')
        Write-Host "Added $Dest to your user PATH."
    }
    if ((@($env:Path -split ';')) -notcontains $Dest) { $env:Path = "$env:Path;$Dest" }
}

Write-Host ''
Write-Host 'Vera is installed.'
Write-Host 'Run it now with:  vera C:\path\to\screening\dev'
Write-Host '(Other terminals that are already open need to be restarted first.)'
