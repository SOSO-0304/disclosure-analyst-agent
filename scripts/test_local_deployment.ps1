[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 18000,

    [ValidateRange(0, 100)]
    [int]$LoadRequests = 12,

    [ValidateRange(1, 16)]
    [int]$Concurrency = 4
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot ".env.perf"
$ComposeFile = Join-Path $ProjectRoot "compose.local-preflight.yaml"
$Report = Join-Path $ProjectRoot "data\quality\local-deployment-preflight.json"
$PostRestartReport = Join-Path $ProjectRoot "data\quality\local-deployment-post-restart.json"
$PreviousToken = $env:DISCLOSURE_API_TOKEN
$PreviousPort = $env:LOCAL_PREFLIGHT_PORT
$Started = $false
$DockerAvailable = $false

function Get-EnvFileValue {
    param([Parameter(Mandatory)][string]$Name)

    $Line = Get-Content $EnvFile -Encoding UTF8 |
        Where-Object { $_ -match "^\s*(?:export\s+)?$([regex]::Escape($Name))\s*=" } |
        Select-Object -First 1
    if (-not $Line) {
        return ""
    }
    $Value = ($Line -replace "^\s*(?:export\s+)?$([regex]::Escape($Name))\s*=", "").Trim()
    if (($Value.StartsWith('"') -and $Value.EndsWith('"')) -or
        ($Value.StartsWith("'") -and $Value.EndsWith("'"))) {
        return $Value.Substring(1, $Value.Length - 2)
    }
    return $Value
}

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments)][string[]]$Arguments)

    & docker compose `
        -p disclosure-local-preflight `
        -f $ComposeFile `
        --env-file $EnvFile `
        @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed: $($Arguments -join ' ')"
    }
}

function Wait-ApiHealthy {
    $Deadline = (Get-Date).AddMinutes(3)
    do {
        try {
            $Response = Invoke-RestMethod `
                -Uri "http://127.0.0.1:$Port/health/live" `
                -TimeoutSec 3
            if ($Response.status -eq "ok") {
                return
            }
        }
        catch {
            Start-Sleep -Seconds 2
        }
    } while ((Get-Date) -lt $Deadline)
    throw "API container did not become healthy within 3 minutes"
}

try {
    Set-Location $ProjectRoot
    if (-not (Test-Path $EnvFile -PathType Leaf)) {
        throw ".env.perf is missing"
    }
    & docker version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Desktop is not running"
    }
    $DockerAvailable = $true
    & python --version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Activate the project virtual environment first"
    }
    $DatabaseRunning = & docker inspect `
        --format "{{.State.Running}}" `
        disclosure-perf-postgres `
        2> $null
    if ($LASTEXITCODE -ne 0 -or $DatabaseRunning -ne "true") {
        throw "disclosure-perf-postgres is not running"
    }

    $PerfDatabaseUrl = if ($env:PERF_DATABASE_URL) {
        $env:PERF_DATABASE_URL
    }
    else {
        Get-EnvFileValue "PERF_DATABASE_URL"
    }
    $ClovaKey = if ($env:CLOVASTUDIO_API_KEY) {
        $env:CLOVASTUDIO_API_KEY
    }
    else {
        Get-EnvFileValue "CLOVASTUDIO_API_KEY"
    }
    if ([string]::IsNullOrWhiteSpace($PerfDatabaseUrl)) {
        throw "PERF_DATABASE_URL is missing from .env.perf"
    }
    if ([string]::IsNullOrWhiteSpace($ClovaKey)) {
        throw "CLOVASTUDIO_API_KEY is missing from the process and .env.perf"
    }

    $TokenBytes = New-Object byte[] 32
    $TokenGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $TokenGenerator.GetBytes($TokenBytes)
    }
    finally {
        $TokenGenerator.Dispose()
    }
    $env:DISCLOSURE_API_TOKEN = (
        [BitConverter]::ToString($TokenBytes) -replace "-", ""
    ).ToLowerInvariant()
    $env:LOCAL_PREFLIGHT_PORT = $Port.ToString()

    Invoke-Compose config --quiet
    $Started = $true
    Invoke-Compose up -d --build
    Wait-ApiHealthy

    python scripts/verify_local_api_preflight.py `
        --base-url "http://127.0.0.1:$Port" `
        --database-url $PerfDatabaseUrl `
        --load-requests $LoadRequests `
        --concurrency $Concurrency `
        --report $Report
    if ($LASTEXITCODE -ne 0) {
        throw "Initial API preflight failed"
    }

    Invoke-Compose restart api
    Wait-ApiHealthy

    python scripts/verify_local_api_preflight.py `
        --base-url "http://127.0.0.1:$Port" `
        --database-url $PerfDatabaseUrl `
        --load-requests 0 `
        --concurrency 1 `
        --report $PostRestartReport
    if ($LASTEXITCODE -ne 0) {
        throw "Post-restart API preflight failed"
    }

    Write-Host ""
    Write-Host "=== local deployment gate ==="
    Write-Host "docker image/build              OK"
    Write-Host "container health                OK"
    Write-Host "authentication                  OK"
    Write-Host "real retrieval                  OK"
    Write-Host "concurrency                     OK"
    Write-Host "restart                         OK"
    Write-Host "database unchanged              OK"
    Write-Host "status                          verified"
}
finally {
    if ($Started -and $DockerAvailable) {
        try {
            Invoke-Compose down
        }
        catch {
            Write-Warning "Preflight container cleanup failed; inspect disclosure-local-preflight"
        }
    }
    if ($null -eq $PreviousToken) {
        Remove-Item Env:DISCLOSURE_API_TOKEN -ErrorAction SilentlyContinue
    }
    else {
        $env:DISCLOSURE_API_TOKEN = $PreviousToken
    }
    if ($null -eq $PreviousPort) {
        Remove-Item Env:LOCAL_PREFLIGHT_PORT -ErrorAction SilentlyContinue
    }
    else {
        $env:LOCAL_PREFLIGHT_PORT = $PreviousPort
    }
    Set-Location $ProjectRoot
}
