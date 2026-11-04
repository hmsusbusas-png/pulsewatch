$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Rand([int]$n) {
    $b = New-Object byte[] $n
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return ([Convert]::ToBase64String($b)).Replace("+", "-").Replace("/", "_").TrimEnd("=")
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Docker not found. Install Docker Desktop and run again." -ForegroundColor Red
    exit 1
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker is not running. Start Docker Desktop, wait until it is ready, run again." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".env")) {
    $fernetBytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($fernetBytes)
    $fernet = [Convert]::ToBase64String($fernetBytes).Replace("+", "-").Replace("/", "_")
    $lines = @(
        "APP_ENV=dev",
        "COOKIE_SECURE=false",
        "PAYMENT_PROVIDER=dev",
        "POSTGRES_USER=pulsewatch",
        "POSTGRES_DB=pulsewatch",
        "POSTGRES_PASSWORD=$(Rand 36)",
        "JWT_SECRET=$(Rand 48)",
        "FERNET_KEY=$fernet",
        "API_BASE_URL=http://localhost:8000",
        "FRONTEND_BASE_URL=http://127.0.0.1:5173",
        'CORS_ORIGINS=["http://127.0.0.1:5173","http://localhost:5173","http://localhost:8080"]',
        "ALLOW_PRIVATE_TARGETS=true",
        "DEMO_EMAIL=demo@example.com"
    )
    [System.IO.File]::WriteAllLines((Join-Path $PSScriptRoot ".env"), $lines)
    Write-Host ".env created with random secrets" -ForegroundColor Green
}

$envPath = Join-Path $PSScriptRoot ".env"
$current = Get-Content $envPath -Raw
$extra = @()
if ($current -notmatch "(?m)^AGENT_TOKEN_FRA=") { $extra += "AGENT_TOKEN_FRA=$(Rand 36)" }
if ($current -notmatch "(?m)^AGENT_TOKEN_AMS=") { $extra += "AGENT_TOKEN_AMS=$(Rand 36)" }
if ($current -notmatch "(?m)^EXTRA_CA_FILE=") { $extra += "EXTRA_CA_FILE=/shared/demo-ca.pem" }
if ($extra.Count -gt 0) {
    [System.IO.File]::AppendAllLines($envPath, [string[]]$extra)
    Write-Host "Demo region tokens added to .env" -ForegroundColor Green
}

Write-Host "Starting postgres, backend, worker, demo target and region agents..." -ForegroundColor Cyan
docker compose --profile demo up --build -d
if ($LASTEXITCODE -ne 0) { Write-Host "compose up failed, see log above" -ForegroundColor Red; exit 1 }

Write-Host "Waiting for backend..." -ForegroundColor Cyan
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/healthz" -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
    Start-Sleep -Seconds 3
}
if (-not $ok) {
    Write-Host "Backend did not come up. Last logs:" -ForegroundColor Red
    docker compose logs --tail 60 backend
    exit 1
}

Write-Host "Migrations..." -ForegroundColor Cyan
docker compose exec -T backend alembic upgrade head

Write-Host "Demo data for 90 days (password is printed ONCE, copy it)..." -ForegroundColor Yellow
docker compose exec -T backend python -m app.seed --demo

if (-not (Test-Path "frontend\node_modules")) {
    Write-Host "npm install..." -ForegroundColor Cyan
    Push-Location frontend
    npm ci
    Pop-Location
}

Write-Host "Starting frontend in a new window..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$PSScriptRoot\frontend'; npm run dev -- --host 127.0.0.1"

Start-Sleep -Seconds 4
Start-Process "http://127.0.0.1:5173"

Write-Host ""
Write-Host "Site:    http://127.0.0.1:5173"
Write-Host "API:     http://127.0.0.1:8000  (docs are off in production only)"
Write-Host "Logs:    docker compose logs -f backend worker"
Write-Host "Stop:    docker compose down   (add -v to wipe the database)"
