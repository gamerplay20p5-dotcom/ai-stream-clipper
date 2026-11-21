<#
Local homologation of PRs #23, #24 and #25 WITHOUT GitHub Actions (Windows PowerShell 5.1+ / PowerShell 7).

  powershell -ExecutionPolicy Bypass -File scripts\homologacao.ps1              full run
  powershell -ExecutionPolicy Bypass -File scripts\homologacao.ps1 -Keep        keep the PostgreSQL container
  powershell -ExecutionPolicy Bypass -File scripts\homologacao.ps1 -NoBrowser   skip Playwright
  powershell -ExecutionPolicy Bypass -File scripts\homologacao.ps1 -Down        stop and remove the container
  ... -Python C:\path	o\python.exe   reuse an existing environment instead of .homologenv

Same steps and report as scripts/homologacao.sh. The database is a disposable container (project "asc-homolog",
127.0.0.1:55432, fake credentials, data in memory). Nothing else on the machine is touched (no global Docker
cleanup, no other project, no real server, never 192.168.1.212). Without Docker the PostgreSQL steps are NOT RUN and
the verdict is PENDENTE, never approved.
#>
param([switch]$Keep, [switch]$NoBrowser, [switch]$Down, [string]$Python = "")

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$Project = "asc-homolog"
$ComposeFile = Join-Path $Root "infrastructure\compose\test-postgres.yml"
$PgUrl = "postgresql+psycopg://asc_test:asc_test@127.0.0.1:55432/postgres"

function Invoke-Compose { param([string[]]$Rest) & docker compose -p $Project -f $ComposeFile @Rest }
if ($Down) { Invoke-Compose @("down", "-v", "--remove-orphans"); exit $LASTEXITCODE }

$Stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$Run = Join-Path $Root "reports\homologacao\$Stamp"
New-Item -ItemType Directory -Force $Run | Out-Null
$Steps = Join-Path $Run "steps.jsonl"
[IO.File]::WriteAllText($Steps, "")
$Py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $Py) { Write-Host "pré-requisito ausente: python"; exit 69 }
foreach ($tool in "git", "node", "npm") { if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { Write-Host "pré-requisito ausente: $tool"; exit 69 } }
$Venv = Join-Path $Root ".homolog\venv"
$VPy = Join-Path $Venv "Scripts\python.exe"
if ($Python) { $VPy = $Python }  # reuse an interpreter that already has the backend dependencies

function Write-StepRecord {
  param([string]$Name, [string]$Status, [double]$Seconds, [string]$Command, [string]$Reason = "")
  $line = @{ step = $Name; status = $Status; seconds = $Seconds; command = $Command; reason = $Reason; required = $true } | ConvertTo-Json -Compress
  [IO.File]::AppendAllText($Steps, $line + "`n", (New-Object Text.UTF8Encoding $false))
}
function Invoke-Step {
  param([string]$Name, [string]$Cwd, [string]$Exe, [string[]]$Arguments, [hashtable]$Env = @{})
  Write-Host "==> $Name"
  $log = Join-Path $Run "$Name.log"
  $saved = @{}
  foreach ($k in $Env.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $Env[$k]) }
  $start = Get-Date
  Push-Location $Cwd
  try {
    # cmd /c keeps native stderr out of PowerShell error records; the whole output goes to the step log
    $argLine = ($Arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join " "
    cmd /c "`"$Exe`" $argLine > `"$log`" 2>&1"
    $code = $LASTEXITCODE
  } finally {
    Pop-Location
    foreach ($k in $Env.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
  }
  $secs = ((Get-Date) - $start).TotalSeconds
  if ($code -eq 0) { Write-StepRecord $Name "passed" $secs "$Exe $argLine"; Write-Host "    ok" }
  else { Write-StepRecord $Name "failed" $secs "$Exe $argLine"; Write-Host "    FALHOU (veja $log)" }
}
function Skip-Step { param([string]$Name, [string]$Command, [string]$Reason)
  Write-StepRecord $Name "not_run" 0 $Command $Reason; Write-Host "==> ${Name}: NÃO EXECUTADO ($Reason)" }

# ---------------------------------------------------------------- prerequisites and environment
$HaveDocker = $false
if (Get-Command docker -ErrorAction SilentlyContinue) {
  cmd /c "docker info >NUL 2>&1"; $a = $LASTEXITCODE
  cmd /c "docker compose version >NUL 2>&1"; $b = $LASTEXITCODE
  $HaveDocker = ($a -eq 0 -and $b -eq 0)
}
$envInfo = [ordered]@{
  commit = (git -C $Root rev-parse HEAD); branch = (git -C $Root branch --show-current)
  dirty = [bool](git -C $Root status --porcelain); os = [Environment]::OSVersion.VersionString + " / " + (Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Caption
  python = (& $Py -c "import platform;print(platform.python_version())"); node = (node --version)
  started = (Get-Date).ToUniversalTime().ToString("s") + "Z"
}
if ($HaveDocker) { $envInfo.docker = (docker --version); $envInfo.compose = (docker compose version) }
[IO.File]::WriteAllText((Join-Path $Run "environment.json"), ($envInfo | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))

try {
  # 1. secrets (needs bash: Git for Windows provides it)
  $bash = (Get-Command bash -ErrorAction SilentlyContinue).Source
  if (-not $bash) {  # Git for Windows installs bash outside PATH
    $bash = @("$env:ProgramFiles\Git\bin\bash.exe", "${env:ProgramFiles(x86)}\Git\bin\bash.exe", "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe") |
      Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
  }
  if ($bash) { Invoke-Step "secrets" $Root $bash @(".github/scripts/check-secrets.sh") }
  else { Skip-Step "secrets" "bash .github/scripts/check-secrets.sh" "bash indisponível (instale o Git for Windows)" }

  # 2. isolated Python environment
  if (-not $Python -and -not (Test-Path $VPy)) { & $Py -m venv $Venv }
  Invoke-Step "python-deps" (Join-Path $Root "backend") $VPy @("-m", "pip", "install", "--quiet", "--upgrade", "pip", "-e", ".[dev,audio,vision]")

  # 3. throwaway PostgreSQL
  if ($HaveDocker) {
    Invoke-Step "postgres-up" $Root "docker" @("compose", "-p", $Project, "-f", $ComposeFile, "up", "-d", "--wait")
    $pgv = (docker exec asc-test-postgres postgres --version)
    $e = Get-Content (Join-Path $Run "environment.json") -Raw | ConvertFrom-Json
    $e | Add-Member -NotePropertyName postgres -NotePropertyValue "$pgv" -Force
    [IO.File]::WriteAllText((Join-Path $Run "environment.json"), ($e | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
  }

  # 4. backend
  $backend = Join-Path $Root "backend"
  Invoke-Step "backend-sqlite" $backend $VPy @("-m", "pytest", "-q", "-m", "not models and not postgres", "-p", "no:cacheprovider", "--junitxml=$Run\backend-sqlite.xml")
  if ($HaveDocker) {
    Invoke-Step "backend-postgres-suite" $backend $VPy @("-m", "pytest", "-q", "-m", "not models and not postgres", "-p", "no:cacheprovider", "--junitxml=$Run\backend-postgres-suite.xml") @{ ASC_TEST_SUITE_DB = "postgres"; ASC_TEST_PG_URL = $PgUrl }
    Invoke-Step "postgres-concurrency-migrations" $backend $VPy @("-m", "pytest", "-q", "-m", "postgres", "-p", "no:cacheprovider", "--junitxml=$Run\postgres-concurrency-migrations.xml", "../tests/test_postgres_shared_lives.py", "../tests/test_postgres_customer_publishing.py", "../tests/test_postgres_migrations.py") @{ ASC_REQUIRE_PG = "1"; ASC_TEST_PG_URL = $PgUrl }
  } else {
    Skip-Step "backend-postgres-suite" "ASC_TEST_SUITE_DB=postgres pytest" "Docker indisponível nesta máquina"
    Skip-Step "postgres-concurrency-migrations" "ASC_REQUIRE_PG=1 pytest -m postgres" "Docker indisponível nesta máquina"
  }

  # 5. commercial service and landing
  $npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source; if (-not $npm) { $npm = "npm" }
  $npx = (Get-Command npx.cmd -ErrorAction SilentlyContinue).Source; if (-not $npx) { $npx = "npx" }
  $commercial = Join-Path $Root "commercial"
  Invoke-Step "commercial-install" $commercial $npm @("ci", "--no-audit", "--no-fund")
  Invoke-Step "commercial-build" $commercial $npm @("run", "build")
  Invoke-Step "commercial-tests" $commercial $npm @("test")
  Invoke-Step "commercial-audit" $commercial $npm @("audit", "--audit-level=high")
  if ($NoBrowser) { Skip-Step "commercial-playwright" "npx playwright test" "-NoBrowser" }
  else { Invoke-Step "commercial-playwright" $commercial $npx @("playwright", "test", "--reporter=line") }

  # 6. dashboard
  $dashboard = Join-Path $Root "dashboard"
  Invoke-Step "dashboard-install" $dashboard $npm @("ci", "--no-audit", "--no-fund")
  Invoke-Step "dashboard-typecheck" $dashboard $npm @("run", "typecheck")
  Invoke-Step "dashboard-i18n" $dashboard $npm @("run", "check:i18n")
  Invoke-Step "dashboard-build" $dashboard $npm @("run", "build")
} finally {
  if ($HaveDocker -and -not $Keep) { Invoke-Compose @("down", "-v", "--remove-orphans") | Out-Null }
}

& $Py (Join-Path $Root "scripts\homologacao_report.py") $Run
exit $LASTEXITCODE
