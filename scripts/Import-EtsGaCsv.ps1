<#
.SYNOPSIS
    Importiert KNX-Gruppenadressen aus einem ETS-GA-CSV-Export als DataPoints in open bridge server.

.DESCRIPTION
    Laedt die CSV-Datei direkt auf den open bridge server hoch. DPT-Erkennung,
    Encoding-Erkennung und DataPoint/Binding-Anlage erfolgen serverseitig
    in einer einzigen Transaktion.

.PARAMETER Url
    Basis-URL der open bridge server-Instanz, z.B. http://localhost:8080

.PARAMETER ApiKey
    API-Schluessel (wird als X-API-Key-Header uebertragen).

.PARAMETER File
    Pfad zur ETS-GA-CSV-Datei.

.PARAMETER Adapter
    Name der KNX-Adapter-Instanz in open bridge server (z.B. "KNX/IP").

.PARAMETER Direction
    Verknuepfungsrichtung: SOURCE (Standard), DEST oder BOTH.

.PARAMETER LogFile
    Optionaler Pfad fuer eine Fehler-Logdatei.

.EXAMPLE
    .\Import-EtsGaCsv.ps1 `
        -Url http://localhost:8080 `
        -ApiKey obs_abc123 `
        -File C:\Export\GA_Export.csv `
        -Adapter "KNX/IP"

.EXAMPLE
    .\Import-EtsGaCsv.ps1 `
        -Url http://192.168.1.10:8080 `
        -ApiKey obs_abc123 `
        -File .\GA_Export.csv `
        -Adapter "KNX TWS" `
        -Direction SOURCE
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Url,
    [Parameter(Mandatory)] [string] $ApiKey,
    [Parameter(Mandatory)] [string] $File,
    [Parameter(Mandatory)] [string] $Adapter,
    [ValidateSet("SOURCE","DEST","BOTH")]
    [string] $Direction = "SOURCE",
    [string] $LogFile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path $File)) { throw "Datei nicht gefunden: $File" }

$arr      = [char]0x2192    # ->
$fileName = [System.IO.Path]::GetFileName($File)
$enc      = [Uri]::EscapeDataString($Adapter)
$uri      = "$($Url.TrimEnd('/'))/api/v1/knxproj/import-csv?adapter_name=$enc&direction=$Direction"

Write-Host "open bridge server ETS-GA-Import" -ForegroundColor Cyan
Write-Host "  Datei   : $File"
Write-Host "  Server  : $uri"
Write-Host "  Adapter : $Adapter"
Write-Host "  Richtung: $Direction"
Write-Host ""
Write-Host "Lade CSV hoch..." -ForegroundColor Yellow

Add-Type -AssemblyName System.Net.Http

$httpClient    = [System.Net.Http.HttpClient]::new()
$httpClient.DefaultRequestHeaders.Add("X-API-Key", $ApiKey)
$fileStream    = [System.IO.File]::OpenRead($File)
$streamContent = [System.Net.Http.StreamContent]::new($fileStream)
$streamContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::new("text/csv")
$form          = [System.Net.Http.MultipartFormDataContent]::new()
$form.Add($streamContent, "file", $fileName)

$sw = [System.Diagnostics.Stopwatch]::StartNew()

try {
    $response = $httpClient.PostAsync($uri, $form).GetAwaiter().GetResult()
    $sw.Stop()
    $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()

    if (-not $response.IsSuccessStatusCode) {
        throw "Server-Fehler $([int]$response.StatusCode): $body"
    }

    $result = $body | ConvertFrom-Json

    Write-Host ""
    Write-Host "Import abgeschlossen" -ForegroundColor Green
    Write-Host "  Neu erstellt : $($result.created)"
    Write-Host "  Aktualisiert : $($result.updated)"
    Write-Host "  Total        : $($result.imported)"
    Write-Host "  Dauer        : $($sw.Elapsed.TotalSeconds.ToString('F1'))s"
    Write-Host "  $arr $($result.message)"
}
catch {
    $sw.Stop()
    $msg = "Import fehlgeschlagen nach $($sw.Elapsed.TotalSeconds.ToString('F1'))s: $_"
    Write-Host ""
    Write-Host $msg -ForegroundColor Red
    if (-not [string]::IsNullOrWhiteSpace($LogFile)) {
        $msg | Out-File -FilePath $LogFile -Encoding UTF8
        Write-Host "  Fehlerprotokoll: $LogFile" -ForegroundColor Yellow
    }
    exit 1
}
finally {
    $fileStream.Dispose()
    $httpClient.Dispose()
}
