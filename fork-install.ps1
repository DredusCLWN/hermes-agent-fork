# fork-install.ps1 — wrapper around the canonical scripts/install.ps1 for this fork.
#
# Why a wrapper instead of forking install.ps1: merge-upstream.sh owns upstream
# files (scripts/, gateway/, run_agent.py, ...). A local copy of install.ps1
# would conflict on every upstream sync. The wrapper adds only fork-specific
# facts: repo URL + tag pinning, then hands off to the canonical installer,
# which already does uv provisioning, venv, package install, path registration,
# git bootstrap and optional desktop build (-IncludeDesktop).
#
# Pin, never main: everything resolves against $Tag. A tag is a stable,
# audited point; main carries in-flight work.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File fork-install.ps1            # default fork + default tag
#   powershell -ExecutionPolicy Bypass -File fork-install.ps1 -Tag v2026.8.10 -SkipSetup
#   powershell -ExecutionPolicy Bypass -File fork-install.ps1 -IncludeDesktop

param(
    [string]$Tag = "v2026.8.10",
    [string]$RepoUrl = "https://github.com/DredusCLWN/hermes-agent-fork",
    [string]$HermesHome = "",
    [switch]$SkipSetup,
    [switch]$IncludeDesktop,
    [switch]$NoShortcut
)

$ErrorActionPreference = "Stop"

if (-not $HermesHome) {
    $HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA "hermes" }
}
$InstallDir = Join-Path $HermesHome "hermes-agent"

Write-Host "== Hermes fork installer =="
Write-Host "Repo : $RepoUrl"
Write-Host "Tag  : $Tag"
Write-Host "Home : $HermesHome"

# 1. git presence. The canonical installer can bootstrap git, but a clear
#    error beats a magic bootstrap for a first-time user.
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git is required (Git for Windows: https://git-scm.com). Install it and re-run."
}

# 2. Clone or re-pin the fork at exactly $Tag.
if (-not (Test-Path (Join-Path $InstallDir ".git"))) {
    Write-Host "`nCloning fork at $Tag ..."
    New-Item -ItemType Directory -Force -Path $HermesHome | Out-Null
    git clone --depth 1 --branch $Tag $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) { throw "clone failed. Is $RepoUrl public and does tag $Tag exist?" }
} else {
    Write-Host "`nInstall dir exists; verifying pin $Tag ..."
    Push-Location $InstallDir
    try {
        $current = git describe --tags --exact-match HEAD 2>$null
        if ($current -ne $Tag) {
            git fetch --depth 1 origin tag $Tag
            if ($LASTEXITCODE -ne 0) { throw "tag $Tag not found on origin" }
            git checkout --detach $Tag
        } else {
            Write-Host "Already at $Tag."
        }
    } finally { Pop-Location }
}

# 3. Hand off to the canonical installer.
$canonical = Join-Path $InstallDir "scripts\install.ps1"
if (-not (Test-Path $canonical)) {
    throw "scripts/install.ps1 not found in $InstallDir. Wrong tag? The tag predates the canonical installer or this is not a hermes checkout."
}

$installArgs = @("-Tag", $Tag)
if ($SkipSetup) { $installArgs += "-SkipSetup" }
if ($IncludeDesktop) { $installArgs += "-IncludeDesktop" }
$installArgs += "-HermesHome", $HermesHome

Write-Host "`nHanding off to canonical installer: scripts/install.ps1 $($installArgs -join ' ')"
& $canonical @installArgs
if ($LASTEXITCODE -ne 0) { throw "canonical install.ps1 failed (exit $LASTEXITCODE)" }

# 4. Fork extras (never touched by upstream sync — local only).
# 4a. .env template — API keys only, user fills them in.
$envFile = Join-Path $HermesHome ".env"
if (-not (Test-Path $envFile)) {
    @(
        "# Hermes fork .env — secrets only (API keys, tokens, passwords).",
        "# Settings live in config.yaml, NOT here.",
        "#",
        "# ANTHROPIC_API_KEY=",
        "# OPENAI_API_KEY=",
        "# DEEPSEEK_API_KEY=",
        ""
    ) | Set-Content -Encoding ascii $envFile
    Write-Host "Created .env template at $envFile (fill in your keys)."
}

# 4b. Desktop shortcut — only when the desktop build exists.
if (-not $NoShortcut) {
    $exeCandidates = @(
        (Join-Path $InstallDir "apps\desktop\dist\Hermes.exe"),
        (Join-Path $InstallDir "apps\desktop\release\Hermes.exe")
    )
    $exe = $exeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($exe) {
        $desktop = [Environment]::GetFolderPath("Desktop")
        $lnk = Join-Path $desktop "Hermes (fork).lnk"
        $ws = New-Object -ComObject WScript.Shell
        $sc = $ws.CreateShortcut($lnk)
        $sc.TargetPath = $exe
        $sc.WorkingDirectory = Split-Path $exe
        $sc.Save()
        Write-Host "Shortcut: $lnk"
    } else {
        Write-Host "Desktop build not found; skipping shortcut (use -IncludeDesktop to build it)."
    }
}

Write-Host "`nDone. Launch: hermes (CLI), hermes --tui, or the desktop app."
Write-Host "Logs: $HermesHome\logs"