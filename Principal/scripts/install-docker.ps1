param(
    [string]$Root = '',
    [switch]$SkipDownload
)

$ErrorActionPreference = 'Stop'
$root = if ($Root) { [System.IO.Path]::GetFullPath($Root).TrimEnd('\') } else { (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path }
$downloads = Join-Path $root 'app\downloads'
$runtime = Join-Path $root 'app\runtime'
$distroPath = Join-Path $runtime 'docker-wsl'
$rootfs = Join-Path $downloads 'ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz'
$rootfsUrl = 'https://cloud-images.ubuntu.com/wsl/releases/noble/current/ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz'
$distro = 'AICorteDocker'
$docker = Join-Path $runtime 'docker\docker.exe'
$drive = $root.Substring(0, 1).ToLowerInvariant()
$tail = $root.Substring(2).Replace('\', '/')
$provision = "/mnt/$drive$tail/Principal/scripts/provision-docker-engine.sh"

New-Item -ItemType Directory -Force -Path $downloads, $runtime | Out-Null

try {
    wsl.exe --status | Out-Null
}
catch {
    throw 'WSL2 nao esta disponivel. Habilite WSL e VirtualMachinePlatform como administrador, reinicie o Windows e execute este script novamente.'
}

$installed = @(wsl.exe --list --quiet 2>$null | ForEach-Object { ($_ -replace "`0", '').Trim() })
if ($installed -notcontains $distro) {
    if (-not (Test-Path -LiteralPath $rootfs)) {
        if ($SkipDownload) { throw "Imagem Ubuntu ausente: $rootfs" }
        & curl.exe -L --fail --retry 3 --retry-delay 5 --output $rootfs $rootfsUrl
        if ($LASTEXITCODE -ne 0) { throw "Falha ao baixar Ubuntu: $LASTEXITCODE" }
    }
    $sums = (Invoke-WebRequest -UseBasicParsing -Uri 'https://cloud-images.ubuntu.com/wsl/releases/noble/current/SHA256SUMS').Content
    $expected = [regex]::Match($sums, '(?m)^([a-f0-9]{64})\s+\*?ubuntu-noble-wsl-amd64-wsl\.rootfs\.tar\.gz$').Groups[1].Value
    if (-not $expected) { throw 'Checksum oficial da imagem Ubuntu nao foi localizado.' }
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $rootfs).Hash.ToLowerInvariant()
    if ($hash -ne $expected) { throw 'Checksum SHA256 da imagem Ubuntu nao confere.' }
    New-Item -ItemType Directory -Force -Path $distroPath | Out-Null
    wsl.exe --import $distro $distroPath $rootfs --version 2
    if ($LASTEXITCODE -ne 0) { throw "Falha ao importar a distribuicao WSL: $LASTEXITCODE" }
}

wsl.exe -d $distro -u root -- bash $provision
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar Docker Engine: $LASTEXITCODE" }
wsl.exe --terminate $distro | Out-Null
Start-Sleep -Seconds 2
Start-Process -FilePath "$env:WINDIR\System32\wsl.exe" `
    -ArgumentList @('-d', $distro, '-u', 'root', '--', 'sleep', 'infinity') `
    -WindowStyle Hidden | Out-Null

$env:DOCKER_HOST = 'tcp://127.0.0.1:2375'
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    & $docker info *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Docker Engine local pronto em $root (tcp://127.0.0.1:2375)."
        exit 0
    }
    Start-Sleep -Milliseconds 500
}
throw 'Docker Engine foi instalado, mas nao respondeu em 30 segundos.'
