param(
    [switch]$SkipGitClean
)

$ErrorActionPreference = "Stop"

function Step($Name) {
    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
}

function Require-Path($Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing required path: $Path"
    }
}

Step "Check required production files"
Require-Path "api\main.py"
Require-Path "api\db.py"
Require-Path "api\Dockerfile"
Require-Path "api\schema.postgres.sql"
Require-Path "api\migrate_sqlite_to_postgres.py"
Require-Path "api\.env.example"
Require-Path "web\package.json"
Require-Path "web\.env.example"
Require-Path "web\vercel.json"
Require-Path "render.yaml"
Require-Path "PRODUCTION_CHECKLIST.md"

Step "Compile Python API modules"
python -B -m py_compile `
    api\db.py `
    api\main.py `
    api\trading.py `
    api\market_ops.py `
    api\migrate_sqlite_to_postgres.py

Step "Type-check Next.js frontend"
npm --prefix web run typecheck

Step "Build Next.js frontend"
npm --prefix web run build

if (-not $SkipGitClean) {
    Step "Validate git working tree"
    $status = git status --short
    if ($status) {
        Write-Host $status
        throw "Working tree has uncommitted changes after preflight."
    }
} else {
    Step "Skip git working tree validation"
}

Write-Host ""
Write-Host "Preflight passed." -ForegroundColor Green
