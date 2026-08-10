<#
.SYNOPSIS
  Produce a reproducible CI failure. Windows / PowerShell twin of break.sh.

.DESCRIPTION
  PowerShell cannot execute a .sh file. Running `./break.sh dependency` in
  PowerShell silently does nothing -- no branch, no push, no red build, and no
  error message to tell you why. This script exists so that never happens.

  The edits themselves live in apply_break.py, shared with break.sh, so the two
  platforms cannot drift apart.

.EXAMPLE
  ./break.ps1 dependency          # branch, commit, push, return to main
  ./break.ps1 subtle -Local       # apply the edits only, no git
  ./break.ps1 -List
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('test_failure', 'dependency', 'lint_type', 'config', 'subtle')]
    [string]$Case,

    [switch]$Local,
    [switch]$List
)

Set-Location -Path $PSScriptRoot

# `python` on Windows, `python3` elsewhere -- pick whichever resolves.
$py = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'python3' }

if ($List) { & $py apply_break.py --list; exit 0 }
if (-not $Case) {
    Write-Host "usage: ./break.ps1 {test_failure|dependency|lint_type|config|subtle} [-Local]"
    exit 1
}

# ---- apply the edits -------------------------------------------------------
$output = & $py apply_break.py $Case
if ($LASTEXITCODE -ne 0) { Write-Error "apply_break.py failed"; exit 1 }

$message = $null
foreach ($line in $output) {
    if ($line -like 'COMMIT_MESSAGE=*') { $message = $line.Substring(15) }
    else { Write-Host $line }
}
if (-not $message) { Write-Error "apply_break.py did not report a commit message"; exit 1 }

if ($Local) {
    Write-Host "-Local: edits applied to the working tree, no git operations."
    exit 0
}

# ---- git -------------------------------------------------------------------
$branch = "break/$Case"
git checkout -B $branch *> $null
if ($LASTEXITCODE -ne 0) { Write-Error "git checkout -B $branch failed"; exit 1 }

git add -A
git commit -q -m $message
if ($LASTEXITCODE -ne 0) { Write-Error "git commit failed"; exit 1 }

git push -u origin $branch --force
if ($LASTEXITCODE -ne 0) { Write-Error "git push failed -- is `origin` set?"; exit 1 }

Write-Host ""
Write-Host "pushed $branch  --  commit message: $message"
Write-Host "Now WAIT for it to go red on the Actions tab, then run:"
Write-Host "  python scripts/run_agent.py <you>/traceme-lab --branch $branch"

git checkout - *> $null
