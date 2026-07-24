$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$sdk = Join-Path $root 'app\runtime\webview2-sdk'
$runtime = Join-Path $PSScriptRoot 'runtime'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$webViewVersion = '1.0.4078.44'

if (-not (Test-Path (Join-Path $sdk 'lib\net462\Microsoft.Web.WebView2.Core.dll'))) {
    $downloads = Join-Path $root 'app\downloads'
    $archive = Join-Path $downloads "Microsoft.Web.WebView2.$webViewVersion.zip"
    New-Item -ItemType Directory -Force -Path $downloads | Out-Null
    Write-Host "Baixando o SDK oficial WebView2 $webViewVersion..."
    Invoke-WebRequest -UseBasicParsing `
        -Uri "https://www.nuget.org/api/v2/package/Microsoft.Web.WebView2/$webViewVersion" `
        -OutFile $archive
    if (Test-Path $sdk) { Remove-Item -LiteralPath $sdk -Recurse -Force }
    Expand-Archive -LiteralPath $archive -DestinationPath $sdk -Force
}

if (-not (Test-Path $compiler)) {
    throw "Compilador .NET Framework 4.8 nao encontrado: $compiler"
}

New-Item -ItemType Directory -Force -Path $runtime | Out-Null
Copy-Item -Force (Join-Path $sdk 'lib\net462\Microsoft.Web.WebView2.Core.dll') $runtime
Copy-Item -Force (Join-Path $sdk 'lib\net462\Microsoft.Web.WebView2.WinForms.dll') $runtime
Copy-Item -Force (Join-Path $sdk 'runtimes\win-x64\native\WebView2Loader.dll') $runtime
Copy-Item -Force (Join-Path $sdk 'LICENSE.txt') $runtime
Copy-Item -Force (Join-Path $sdk 'NOTICE.txt') $runtime

$output = Join-Path $root 'AICorte.exe'
$core = Join-Path $runtime 'Microsoft.Web.WebView2.Core.dll'
$winforms = Join-Path $runtime 'Microsoft.Web.WebView2.WinForms.dll'
$source = Join-Path $PSScriptRoot 'AICorteShell.cs'
$arguments = @(
    '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
    "/out:$output",
    '/reference:System.dll', '/reference:System.Core.dll', '/reference:System.Drawing.dll',
    '/reference:System.Windows.Forms.dll', '/reference:System.Web.Extensions.dll',
    "/reference:$core", "/reference:$winforms", $source
)
& $compiler $arguments
if ($LASTEXITCODE -ne 0) { throw "Falha ao compilar AICorte.exe: $LASTEXITCODE" }
Write-Host "AICorte.exe criado em $root"
