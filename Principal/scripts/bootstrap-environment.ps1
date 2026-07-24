param([Parameter(Mandatory = $true)][string]$Root)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$rootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
$app = Join-Path $rootPath 'app'
$runtime = Join-Path $app 'runtime'
$downloads = Join-Path $app 'downloads'
$tmp = Join-Path $app 'tmp'

function Send-Progress([string]$Id, [string]$State, [int]$Percent, [string]$Message) {
    Write-Output "AICORTE_PROGRESS|$Id|$State|$Percent|$Message"
}

function Download-File([string]$Url, [string]$Destination) {
    $part = "$Destination.part"
    if (Test-Path -LiteralPath $part) { [System.IO.File]::Delete($part) }
    Invoke-WebRequest -UseBasicParsing -Headers @{ 'User-Agent' = 'AICorte/3.0' } -Uri $Url -OutFile $part
    Move-Item -Force -LiteralPath $part -Destination $Destination
}

function Set-CurrentJunction([string]$Target, [string]$Current) {
    if (Test-Path -LiteralPath $Current) { [System.IO.Directory]::Delete($Current) }
    New-Item -ItemType Junction -Path $Current -Target $Target | Out-Null
}

New-Item -ItemType Directory -Force -Path $rootPath, $app, $runtime, $downloads, $tmp, `
    (Join-Path $rootPath 'AI'), (Join-Path $rootPath 'PROJETOS'), `
    (Join-Path $rootPath 'Principal\state'), (Join-Path $rootPath 'Principal\logs') | Out-Null
Send-Progress 'root' 'ready' 5 "Raiz pronta em $rootPath"

$git = Join-Path $runtime 'git\current\cmd\git.exe'
if (-not (Test-Path -LiteralPath $git)) {
    Send-Progress 'git' 'downloading' 10 'Baixando Git portatil'
    $release = Invoke-RestMethod -Headers @{ 'User-Agent' = 'AICorte/3.0' } -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest'
    $asset = $release.assets | Where-Object { $_.name -match '^MinGit-.*-64-bit\.zip$' } | Select-Object -First 1
    if (-not $asset) { throw 'O release atual do Git for Windows nao contem MinGit 64-bit.' }
    $archive = Join-Path $downloads $asset.name
    if (-not (Test-Path -LiteralPath $archive)) { Download-File $asset.browser_download_url $archive }
    $version = [System.IO.Path]::GetFileNameWithoutExtension($asset.name)
    $target = Join-Path $runtime "git\$version"
    if (-not (Test-Path -LiteralPath $target)) { Expand-Archive -LiteralPath $archive -DestinationPath $target -Force }
    Set-CurrentJunction $target (Join-Path $runtime 'git\current')
}
& $git --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Git portatil nao respondeu.' }
Send-Progress 'git' 'ready' 24 'Git portatil pronto'

$pythonRoot = Join-Path $runtime 'uv-python'
$python = Get-ChildItem -LiteralPath $pythonRoot -Directory -Filter 'cpython-3.11-windows-*' -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'python.exe' } |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) {
    Send-Progress 'python' 'downloading' 28 'Baixando gerenciador Python portatil'
    $uvFolder = Join-Path $runtime 'uv'
    $uv = Join-Path $uvFolder 'uv.exe'
    if (-not (Test-Path -LiteralPath $uv)) {
        $uvArchive = Join-Path $downloads 'uv-x86_64-pc-windows-msvc.zip'
        Download-File 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' $uvArchive
        New-Item -ItemType Directory -Force -Path $uvFolder | Out-Null
        Expand-Archive -LiteralPath $uvArchive -DestinationPath $uvFolder -Force
    }
    Send-Progress 'python' 'installing' 35 'Instalando CPython 3.11 na raiz escolhida'
    $env:UV_PYTHON_INSTALL_DIR = $pythonRoot
    $env:UV_PYTHON_BIN_DIR = Join-Path $runtime 'python-bin'
    $env:UV_CACHE_DIR = Join-Path $app 'cache\uv'
    & $uv python install 3.11 --no-registry --no-progress --install-dir $pythonRoot
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar CPython 3.11 com uv.' }
    $python = Get-ChildItem -LiteralPath $pythonRoot -Directory -Filter 'cpython-3.11-windows-*' |
        Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'python.exe' } |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
& $python -c "import json, sqlite3, http.server; print('ok')" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Python portatil nao passou na validacao.' }
Send-Progress 'python' 'ready' 46 'Python portatil pronto'

$dockerFolder = Join-Path $runtime 'docker'
$docker = Join-Path $dockerFolder 'docker.exe'
if (-not (Test-Path -LiteralPath $docker)) {
    Send-Progress 'docker' 'downloading' 50 'Baixando Docker CLI portatil'
    $index = Invoke-WebRequest -UseBasicParsing -Uri 'https://download.docker.com/win/static/stable/x86_64/'
    $versions = foreach ($link in $index.Links) {
        if ($link.href -match '^docker-([0-9]+(?:\.[0-9]+){2})\.zip$') {
            [pscustomobject]@{ Version = [version]$Matches[1]; File = $link.href }
        }
    }
    $latest = $versions | Sort-Object Version -Descending | Select-Object -First 1
    if (-not $latest) { throw 'Nao foi possivel localizar o Docker CLI estavel para Windows.' }
    $archive = Join-Path $downloads $latest.File
    if (-not (Test-Path -LiteralPath $archive)) { Download-File "https://download.docker.com/win/static/stable/x86_64/$($latest.File)" $archive }
    $extract = Join-Path $tmp 'docker-cli'
    if (Test-Path -LiteralPath $extract) { [System.IO.Directory]::Delete($extract, $true) }
    Expand-Archive -LiteralPath $archive -DestinationPath $extract -Force
    New-Item -ItemType Directory -Force -Path $dockerFolder | Out-Null
    Copy-Item -Force (Join-Path $extract 'docker\docker.exe') $docker
}
& $docker --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker CLI portatil nao respondeu.' }
Send-Progress 'docker' 'ready' 60 'Docker CLI pronto'

$compose = Join-Path $dockerFolder 'docker-compose.exe'
if (-not (Test-Path -LiteralPath $compose)) {
    Send-Progress 'compose' 'downloading' 63 'Baixando Docker Compose portatil'
    $release = Invoke-RestMethod -Headers @{ 'User-Agent' = 'AICorte/3.0' } -Uri 'https://api.github.com/repos/docker/compose/releases/latest'
    $asset = $release.assets | Where-Object { $_.name -eq 'docker-compose-windows-x86_64.exe' } | Select-Object -First 1
    if (-not $asset) { throw 'O release atual do Docker Compose nao contem o executavel Windows x64.' }
    Download-File $asset.browser_download_url $compose
}
& $compose version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose portatil nao respondeu.' }
Send-Progress 'compose' 'ready' 70 'Docker Compose pronto'

$env:DOCKER_HOST = 'tcp://127.0.0.1:2375'
& $docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Send-Progress 'engine' 'installing' 74 'Preparando Docker Engine local no WSL2'
    & (Join-Path $rootPath 'Principal\scripts\install-docker.ps1') -Root $rootPath
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao preparar Docker Engine local.' }
}
& $docker info *> $null
if ($LASTEXITCODE -ne 0) { throw 'Docker Engine foi instalado, mas nao respondeu.' }
Send-Progress 'engine' 'ready' 94 'Docker Engine local pronto'

$environment = @{
    root = $rootPath
    docker_distro = 'AICorteDocker'
    docker_host = 'tcp://127.0.0.1:2375'
    configured_at = (Get-Date).ToString('o')
} | ConvertTo-Json
$environment | Set-Content -LiteralPath (Join-Path $rootPath 'Principal\state\environment.json') -Encoding UTF8
Send-Progress 'complete' 'ready' 100 'Ambiente concluido'
