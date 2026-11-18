<#
.SYNOPSIS
    Chrome/ChromeDriver do host para o worker local de Chordify.

.DESCRIPTION
    O container idb_chordify_worker nao consegue usar o perfil do Chrome do
    Windows diretamente (cookies cifrados com DPAPI + perfil bloqueado). Este
    script mantem um ChromeDriver no HOST, com um perfil DEDICADO do Chrome,
    e o worker se conecta via Selenium Remote em host.docker.internal:9515.

    Fluxo inicial:
      1) .\start_chordify_browser.ps1 -Setup
         Abre o Chrome no perfil dedicado. Faca login no Chordify (e resolva
         o Cloudflare) uma unica vez; depois feche essa janela do Chrome.
      2) .\start_chordify_browser.ps1 -Start
         Baixa o ChromeDriver compativel com o seu Chrome e inicia na porta 9515.
      3) Suba o worker:
         docker compose -f docker-compose.worker.yml up --build -d

    Se os cookies expirarem e a API voltar a falhar, rode -Setup novamente.

.PARAMETER Setup
    Cria/abre o perfil dedicado para login manual no Chordify.

.PARAMETER Start
    Baixa (se necessario) e inicia o ChromeDriver na porta configurada.

.PARAMETER Stop
    Encerra o ChromeDriver e o Chrome aberto com o perfil dedicado.

.PARAMETER Status
    Mostra versoes, caminhos, PID e se a porta esta respondendo.

.PARAMETER Port
    Porta do ChromeDriver (default: 9515).

.PARAMETER ProfileDirectory
    Diretorio do perfil dentro do user-data-dir (default: Default).

.PARAMETER AllowedIps
    Lista de IPs autorizados a conectar no ChromeDriver (default: 0.0.0.0).
    O ChromeDriver continua ouvindo apenas em 127.0.0.1/::1; isso apenas
    permite a conexao encaminhada pelo Docker Desktop.

.PARAMETER AllowedOrigins
    Origens/Hosts aceitos pelo ChromeDriver (default: host.docker.internal,
    127.0.0.1 e localhost na porta configurada). Sem isso o ChromeDriver 115+
    rejeita o header Host enviado pelo container.
#>
param(
    [switch]$Setup,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status,
    [int]$Port = 9515,
    [string]$ProfileDirectory = "Default",
    [string]$AllowedIps = "0.0.0.0",
    [string]$AllowedOrigins = ""
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = "SilentlyContinue"

$BaseDir = Join-Path $env:LOCALAPPDATA "idb-chordify"
$ProfileRoot = Join-Path $BaseDir "chrome-profile"
$LogPath = Join-Path $BaseDir "chromedriver.log"
$PidPath = Join-Path $BaseDir "chromedriver.pid"

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Get-ChromePath {
    $candidates = @(
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
    )
    foreach ($path in $candidates) {
        if (Test-Path -LiteralPath $path) { return $path }
    }
    throw "Google Chrome nao encontrado. Instale o Chrome e rode novamente."
}

function Get-ChromeVersion([string]$ChromePath) {
    return (Get-Item -LiteralPath $ChromePath).VersionInfo.ProductVersion
}

function Get-DriverDir([string]$Major) {
    return (Join-Path $BaseDir "chromedriver-$Major")
}

function Ensure-ChromeDriver([string]$ChromeVersion) {
    $major = $ChromeVersion.Split(".")[0]
    $driverDir = Get-DriverDir $major
    $driverExe = Join-Path $driverDir "chromedriver.exe"

    if (Test-Path -LiteralPath $driverExe) {
        return $driverExe
    }

    New-Item -ItemType Directory -Force -Path $BaseDir, $driverDir | Out-Null

    Write-Step "Baixando ChromeDriver compativel com Chrome $ChromeVersion..."
    $url = "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"
    $meta = Invoke-RestMethod -Uri $url -UseBasicParsing

    $entry = $meta.versions |
        Where-Object { $_.version -eq $ChromeVersion } |
        Select-Object -Last 1

    if (-not $entry) {
        $entry = $meta.versions |
            Where-Object { $_.version -like "$major.*" } |
            Sort-Object { [version]$_.version } |
            Select-Object -Last 1
    }

    if (-not $entry) {
        throw "Nenhum ChromeDriver encontrado para o Chrome $ChromeVersion (major $major)."
    }

    $download = $entry.downloads.chromedriver |
        Where-Object { $_.platform -eq "win64" } |
        Select-Object -First 1

    if (-not $download) {
        throw "ChromeDriver win64 indisponivel para a versao $($entry.version)."
    }

    $tmpDir = Join-Path $env:TEMP ("idb-chromedriver-" + [guid]::NewGuid().ToString("N"))
    $zipPath = Join-Path $tmpDir "chromedriver.zip"
    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

    try {
        Invoke-WebRequest -Uri $download.url -OutFile $zipPath -UseBasicParsing
        Expand-Archive -LiteralPath $zipPath -DestinationPath $tmpDir -Force
        $extracted = Join-Path $tmpDir "chromedriver-win64\chromedriver.exe"
        if (-not (Test-Path -LiteralPath $extracted)) {
            throw "chromedriver.exe nao encontrado no zip baixado."
        }
        Copy-Item -LiteralPath $extracted -Destination $driverExe -Force
        Write-Step "ChromeDriver $($entry.version) salvo em $driverExe"
    }
    finally {
        Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    return $driverExe
}

function Get-RunningDriver {
    if (-not (Test-Path -LiteralPath $PidPath)) { return $null }
    $rawPid = (Get-Content -LiteralPath $PidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $rawPid) { return $null }
    $proc = Get-Process -Id ([int]$rawPid) -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -like "chromedriver*") { return $proc }
    return $null
}

function Test-DriverPort([int]$PortNumber) {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$PortNumber/status" -UseBasicParsing -TimeoutSec 3
        return ($response.StatusCode -eq 200)
    }
    catch {
        return $false
    }
}

function Open-Profile([string]$ChromePath) {
    New-Item -ItemType Directory -Force -Path $BaseDir, $ProfileRoot | Out-Null
    Write-Step "Abrindo o perfil dedicado em $ProfileRoot"
    Write-Host "Faca login no Chordify e resolva o Cloudflare. Depois FECHE essa janela." -ForegroundColor Yellow
    Start-Process -FilePath $ChromePath -ArgumentList @(
        "--user-data-dir=$ProfileRoot",
        "--profile-directory=$ProfileDirectory",
        "--no-first-run",
        "--no-default-browser-check",
        "https://chordify.net/"
    )
}

function Stop-Driver([string]$ProfileRoot) {
    $proc = Get-RunningDriver
    if ($proc) {
        Write-Step "Encerrando ChromeDriver (PID $($proc.Id))..."
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue

    $chromeProcs = Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($ProfileRoot) }
    foreach ($chrome in $chromeProcs) {
        Stop-Process -Id $chrome.ProcessId -Force -ErrorAction SilentlyContinue
    }

    Write-Step "Parado."
}

if (-not ($Setup -or $Start -or $Stop -or $Status)) {
    Write-Host "Uso:" -ForegroundColor Yellow
    Write-Host "  .\start_chordify_browser.ps1 -Setup    # login unico no perfil dedicado"
    Write-Host "  .\start_chordify_browser.ps1 -Start    # inicia o ChromeDriver na porta $Port"
    Write-Host "  .\start_chordify_browser.ps1 -Status"
    Write-Host "  .\start_chordify_browser.ps1 -Stop"
    exit 0
}

$chromePath = Get-ChromePath
$chromeVersion = Get-ChromeVersion $chromePath
$major = $chromeVersion.Split(".")[0]
$driverExe = Join-Path (Get-DriverDir $major) "chromedriver.exe"

if ($Setup) {
    Open-Profile $chromePath
}

if ($Stop) {
    Stop-Driver $ProfileRoot
}

if ($Start) {
    $running = Get-RunningDriver
    if ($running) {
        Write-Step "ChromeDriver ja esta rodando (PID $($running.Id), porta $Port)."
    }
    else {
        $driverExe = Ensure-ChromeDriver $chromeVersion
        New-Item -ItemType Directory -Force -Path $BaseDir | Out-Null

        if (-not $AllowedOrigins) {
            $AllowedOrigins = @(
                "http://host.docker.internal:$Port",
                "http://127.0.0.1:$Port",
                "http://localhost:$Port"
            ) -join ","
        }

        $driverArgs = @(
            "--port=$Port",
            "--url-base=/",
            "--verbose",
            "--log-path=$LogPath",
            "--allowed-ips=$AllowedIps",
            "--allowed-origins=$AllowedOrigins"
        )

        Write-Step "Iniciando ChromeDriver $chromeVersion na porta $Port..."
        $proc = Start-Process -FilePath $driverExe -ArgumentList $driverArgs -WindowStyle Hidden -PassThru
        Set-Content -LiteralPath $PidPath -Value $proc.Id -Encoding ASCII

        Start-Sleep -Seconds 2
        if (Test-DriverPort $Port) {
            Write-Step "ChromeDriver respondendo em http://127.0.0.1:$Port (PID $($proc.Id))."
        }
        else {
            Write-Warning "ChromeDriver iniciou (PID $($proc.Id)), mas a porta $Port ainda nao respondeu. Veja $LogPath."
        }
    }

    Write-Host ""
    Write-Host "No arquivo .env.worker use:" -ForegroundColor Yellow
    Write-Host "  SELENIUM_REMOTE_URL=http://host.docker.internal:$Port"
    Write-Host "  CHROME_HOST_BIN=$chromePath"
    Write-Host "  CHROME_HOST_USER_DATA_DIR=$ProfileRoot"
    Write-Host "  CHROME_HOST_PROFILE_DIRECTORY=$ProfileDirectory"
    Write-Host "  CHROME_HEADLESS=false"
}

if ($Status) {
    Write-Host "Chrome:        $chromePath ($chromeVersion)"
    Write-Host "Perfil:        $ProfileRoot (profile-directory=$ProfileDirectory)"
    Write-Host "ChromeDriver:  $driverExe"
    if (Test-Path -LiteralPath $driverExe) {
        Write-Host "Driver versao: $(& $driverExe --version)"
    }
    $proc = Get-RunningDriver
    if ($proc) {
        Write-Host "Processo:      PID $($proc.Id) em execucao"
    }
    else {
        Write-Host "Processo:      parado"
    }
    Write-Host "Porta $Port`:    $(if (Test-DriverPort $Port) { 'respondendo' } else { 'fechada' })"
    Write-Host "Log:           $LogPath"
}
