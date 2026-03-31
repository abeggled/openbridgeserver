<#
.SYNOPSIS
    Importiert KNX-Gruppenadressen aus einem ETS-GA-CSV-Export als DataPoints in openTWS.

.DESCRIPTION
    Liest eine CSV-Datei (ETS 5/6 GA-Export, Semikolon- oder Komma-getrennt) und legt
    je GA einen DataPoint mit passendem Typ und Einheit an. Anschliessend wird eine
    SOURCE-Verknüpfung zur angegebenen KNX-Adapter-Instanz erstellt.

    Fehlgeschlagene GAs werden am Ende ausgegeben oder in eine Logdatei geschrieben.
    Der Import läuft bei Einzelfehlern weiter.

.PARAMETER Url
    Basis-URL der openTWS-Instanz, z.B. http://localhost:8080

.PARAMETER ApiKey
    API-Schlüssel (wird als X-API-Key-Header übertragen).

.PARAMETER File
    Pfad zur ETS-GA-CSV-Datei.

.PARAMETER Adapter
    Name der KNX-Adapter-Instanz in openTWS (z.B. "KNX/IP").

.PARAMETER LogFile
    Optionaler Pfad für eine Fehler-Logdatei. Wenn nicht angegeben, werden Fehler
    am Ende auf der Konsole ausgegeben.

.PARAMETER Direction
    Verknüpfungsrichtung: SOURCE (Standard), DEST oder BOTH.

.PARAMETER Encoding
    Zeichenkodierung der CSV-Datei: Auto (Standard), UTF8 oder Default (ANSI/Windows-1252).
    Auto erkennt anhand des BOM automatisch ob UTF-8 oder Windows-1252 vorliegt.
    ETS 5 exportiert standardmässig ANSI (kein BOM), ETS 6 UTF-8 mit BOM.

.EXAMPLE
    .\Import-EtsGaCsv.ps1 `
        -Url http://localhost:8080 `
        -ApiKey opentws_abc123 `
        -File C:\Export\GA_Export.csv `
        -Adapter "KNX/IP"

.EXAMPLE
    .\Import-EtsGaCsv.ps1 `
        -Url http://localhost:8080 `
        -ApiKey opentws_abc123 `
        -File C:\Export\GA_Export.csv `
        -Adapter "KNX/IP" `
        -Encoding Default

.EXAMPLE
    .\Import-EtsGaCsv.ps1 `
        -Url http://192.168.1.10:8080 `
        -ApiKey opentws_abc123 `
        -File .\GA_Export.csv `
        -Adapter "KNX/IP" `
        -LogFile .\import_errors.log `
        -Direction SOURCE
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Url,
    [Parameter(Mandatory)] [string] $ApiKey,
    [Parameter(Mandatory)] [string] $File,
    [Parameter(Mandatory)] [string] $Adapter,
    [string] $LogFile,
    [ValidateSet("SOURCE","DEST","BOTH")]
    [string] $Direction = "SOURCE",
    [ValidateSet("Auto","UTF8","Default")]
    [string] $Encoding = "Auto"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# DPT-Mapping: ETS-Format → openTWS (dpt_id, data_type, unit)
# ETS liefert: "DPST-9-1"  oder "DPT-9"
# openTWS:     "DPT9.001"
# ---------------------------------------------------------------------------
$deg  = [char]176        # °
$ohm  = [char]0x03A9    # Ω
$sup2 = [char]178       # ²
$sup3 = [char]179       # ³
$arr  = [char]0x2192    # →

$DptMap = @{
    # DPT 1 — 1-Bit Boolean (vollständig nach KNX-Spec)
    "DPT1.001"  = @{ data_type="BOOLEAN"; unit="" }  # Switch
    "DPT1.002"  = @{ data_type="BOOLEAN"; unit="" }  # Bool
    "DPT1.003"  = @{ data_type="BOOLEAN"; unit="" }  # Enable
    "DPT1.004"  = @{ data_type="BOOLEAN"; unit="" }  # Ramp
    "DPT1.005"  = @{ data_type="BOOLEAN"; unit="" }  # Alarm
    "DPT1.006"  = @{ data_type="BOOLEAN"; unit="" }  # Binary Value
    "DPT1.007"  = @{ data_type="BOOLEAN"; unit="" }  # Step
    "DPT1.008"  = @{ data_type="BOOLEAN"; unit="" }  # Up/Down
    "DPT1.009"  = @{ data_type="BOOLEAN"; unit="" }  # Open/Close
    "DPT1.010"  = @{ data_type="BOOLEAN"; unit="" }  # Start/Stop
    "DPT1.011"  = @{ data_type="BOOLEAN"; unit="" }  # State
    "DPT1.012"  = @{ data_type="BOOLEAN"; unit="" }  # Invert
    "DPT1.013"  = @{ data_type="BOOLEAN"; unit="" }  # Dim Send Style
    "DPT1.014"  = @{ data_type="BOOLEAN"; unit="" }  # Input Source
    "DPT1.015"  = @{ data_type="BOOLEAN"; unit="" }  # Reset
    "DPT1.016"  = @{ data_type="BOOLEAN"; unit="" }  # Ack
    "DPT1.017"  = @{ data_type="BOOLEAN"; unit="" }  # Trigger
    "DPT1.018"  = @{ data_type="BOOLEAN"; unit="" }  # Occupancy
    "DPT1.019"  = @{ data_type="BOOLEAN"; unit="" }  # Window/Door
    "DPT1.021"  = @{ data_type="BOOLEAN"; unit="" }  # Logical Function
    "DPT1.022"  = @{ data_type="BOOLEAN"; unit="" }  # Scene A/B
    "DPT1.023"  = @{ data_type="BOOLEAN"; unit="" }  # Shutter/Blinds Mode
    "DPT1.024"  = @{ data_type="BOOLEAN"; unit="" }  # Day/Night
    # DPT 3 — 4-Bit relative Steuerung
    "DPT3.007"  = @{ data_type="INTEGER"; unit="" }  # Dimmen
    "DPT3.008"  = @{ data_type="INTEGER"; unit="" }  # Jalousie
    # DPT 5 — 8-Bit unsigned
    "DPT5.001"  = @{ data_type="FLOAT";   unit="%" }
    "DPT5.003"  = @{ data_type="FLOAT";   unit=$deg }  # Angle 0-255 → 0°-360°
    "DPT5.004"  = @{ data_type="INTEGER"; unit="%" }
    "DPT5.005"  = @{ data_type="INTEGER"; unit="" }
    "DPT5.006"  = @{ data_type="INTEGER"; unit="" }
    "DPT5.010"  = @{ data_type="INTEGER"; unit="" }
    # DPT 6 — 8-Bit signed
    "DPT6.001"  = @{ data_type="INTEGER"; unit="%" }
    "DPT6.010"  = @{ data_type="INTEGER"; unit="" }
    # DPT 7 — 16-Bit unsigned
    "DPT7.001"  = @{ data_type="INTEGER"; unit="" }
    "DPT7.002"  = @{ data_type="INTEGER"; unit="ms" }
    "DPT7.003"  = @{ data_type="INTEGER"; unit="ms" }
    "DPT7.004"  = @{ data_type="INTEGER"; unit="ms" }
    "DPT7.005"  = @{ data_type="INTEGER"; unit="s" }
    "DPT7.006"  = @{ data_type="INTEGER"; unit="min" }
    "DPT7.007"  = @{ data_type="INTEGER"; unit="h" }
    "DPT7.011"  = @{ data_type="INTEGER"; unit="mm" }
    "DPT7.012"  = @{ data_type="INTEGER"; unit="mA" }
    "DPT7.013"  = @{ data_type="INTEGER"; unit="lx" }
    "DPT7.600"  = @{ data_type="INTEGER"; unit="K" }
    # DPT 8 — 16-Bit signed
    "DPT8.001"  = @{ data_type="INTEGER"; unit="" }
    "DPT8.002"  = @{ data_type="INTEGER"; unit="ms" }
    "DPT8.003"  = @{ data_type="INTEGER"; unit="ms" }
    "DPT8.004"  = @{ data_type="INTEGER"; unit="ms" }
    "DPT8.005"  = @{ data_type="INTEGER"; unit="s" }
    "DPT8.006"  = @{ data_type="INTEGER"; unit="min" }
    "DPT8.007"  = @{ data_type="INTEGER"; unit="h" }
    "DPT8.010"  = @{ data_type="INTEGER"; unit="%" }
    "DPT8.011"  = @{ data_type="INTEGER"; unit=$deg }
    "DPT8.012"  = @{ data_type="INTEGER"; unit="m" }
    # DPT 9 — 16-Bit Gleitkomma (vollständig nach KNX-Spec)
    "DPT9.001"  = @{ data_type="FLOAT";   unit=($deg + "C") }
    "DPT9.002"  = @{ data_type="FLOAT";   unit="K" }
    "DPT9.003"  = @{ data_type="FLOAT";   unit="K/h" }
    "DPT9.004"  = @{ data_type="FLOAT";   unit="lx" }
    "DPT9.005"  = @{ data_type="FLOAT";   unit="m/s" }
    "DPT9.006"  = @{ data_type="FLOAT";   unit="Pa" }
    "DPT9.007"  = @{ data_type="FLOAT";   unit="%" }
    "DPT9.008"  = @{ data_type="FLOAT";   unit="ppm" }
    "DPT9.009"  = @{ data_type="FLOAT";   unit=("m" + $sup3 + "/h") }
    "DPT9.010"  = @{ data_type="FLOAT";   unit="s" }
    "DPT9.011"  = @{ data_type="FLOAT";   unit="ms" }
    "DPT9.020"  = @{ data_type="FLOAT";   unit="mV" }
    "DPT9.021"  = @{ data_type="FLOAT";   unit="mA" }
    "DPT9.022"  = @{ data_type="FLOAT";   unit=("W/m" + $sup2) }   # Power Density
    "DPT9.023"  = @{ data_type="FLOAT";   unit="K/%" }             # Kelvin/Percent
    "DPT9.024"  = @{ data_type="FLOAT";   unit="kW" }              # Power (nicht W/m²!)
    "DPT9.025"  = @{ data_type="FLOAT";   unit="l/h" }             # Volume Flow
    "DPT9.026"  = @{ data_type="FLOAT";   unit=("l/m" + $sup2) }
    "DPT9.027"  = @{ data_type="FLOAT";   unit=($deg + "F") }
    "DPT9.028"  = @{ data_type="FLOAT";   unit="km/h" }
    "DPT9.029"  = @{ data_type="FLOAT";   unit=("g/m" + $sup3) }
    "DPT9.030"  = @{ data_type="FLOAT";   unit="µg/m3" }
    # DPT 10/11/16/19 — Zeit/Datum/Text
    "DPT10.001" = @{ data_type="TIME";    unit="" }
    "DPT11.001" = @{ data_type="DATE";    unit="" }
    "DPT16.000" = @{ data_type="STRING";  unit="" }
    "DPT16.001" = @{ data_type="STRING";  unit="" }
    "DPT19.001" = @{ data_type="STRING";  unit="" }
    # DPT 12/13 — 32-Bit Zähler
    "DPT12.001" = @{ data_type="INTEGER"; unit="" }
    "DPT13.001" = @{ data_type="INTEGER"; unit="" }
    "DPT13.010" = @{ data_type="INTEGER"; unit="Wh" }
    # DPT 14 — 32-Bit IEEE float (korrigiert nach KNX-Spec v02.02.01)
    "DPT14.000" = @{ data_type="FLOAT";   unit=("m/s" + $sup2) }
    "DPT14.006" = @{ data_type="FLOAT";   unit="rad" }
    "DPT14.007" = @{ data_type="FLOAT";   unit=$deg }
    "DPT14.009" = @{ data_type="FLOAT";   unit="rad/s" }
    "DPT14.010" = @{ data_type="FLOAT";   unit=("m" + $sup2) }
    "DPT14.011" = @{ data_type="FLOAT";   unit="F" }
    "DPT14.017" = @{ data_type="FLOAT";   unit=("kg/m" + $sup3) }  # Density
    "DPT14.018" = @{ data_type="FLOAT";   unit="C" }               # Electric Charge
    "DPT14.019" = @{ data_type="FLOAT";   unit="A" }               # Electric Current
    "DPT14.027" = @{ data_type="FLOAT";   unit="V" }               # Electric Potential
    "DPT14.028" = @{ data_type="FLOAT";   unit="V" }               # Electric Potential Diff
    "DPT14.030" = @{ data_type="FLOAT";   unit="V" }               # EMF
    "DPT14.031" = @{ data_type="FLOAT";   unit="J" }               # Energy (nicht Hz!)
    "DPT14.032" = @{ data_type="FLOAT";   unit="N" }               # Force
    "DPT14.033" = @{ data_type="FLOAT";   unit="Hz" }              # Frequency
    "DPT14.036" = @{ data_type="FLOAT";   unit="W" }               # Heat Flow Rate
    "DPT14.037" = @{ data_type="FLOAT";   unit="J" }               # Heat Quantity
    "DPT14.038" = @{ data_type="FLOAT";   unit=$ohm }              # Impedance
    "DPT14.039" = @{ data_type="FLOAT";   unit="m" }               # Length
    "DPT14.042" = @{ data_type="FLOAT";   unit="lm" }              # Luminous Flux
    "DPT14.043" = @{ data_type="FLOAT";   unit="cd" }              # Luminous Intensity
    "DPT14.051" = @{ data_type="FLOAT";   unit="kg" }              # Mass
    "DPT14.052" = @{ data_type="FLOAT";   unit="kg/s" }            # Mass Flux
    "DPT14.054" = @{ data_type="FLOAT";   unit="rad" }             # Phase Angle rad (nicht W!)
    "DPT14.055" = @{ data_type="FLOAT";   unit=$deg }              # Phase Angle deg
    "DPT14.056" = @{ data_type="FLOAT";   unit="W" }               # Power
    "DPT14.057" = @{ data_type="FLOAT";   unit="" }                # Power Factor (nicht var!)
    "DPT14.058" = @{ data_type="FLOAT";   unit="Pa" }              # Pressure
    "DPT14.059" = @{ data_type="FLOAT";   unit=$ohm }              # Reactance
    "DPT14.060" = @{ data_type="FLOAT";   unit=$ohm }              # Resistance
    "DPT14.062" = @{ data_type="FLOAT";   unit="H" }               # Self Inductance
    "DPT14.065" = @{ data_type="FLOAT";   unit="m/s" }             # Speed
    "DPT14.067" = @{ data_type="FLOAT";   unit="N/m" }             # Surface Tension (nicht V!)
    "DPT14.068" = @{ data_type="FLOAT";   unit=($deg + "C") }      # Temperature common
    "DPT14.069" = @{ data_type="FLOAT";   unit="K" }               # Temperature absolute
    "DPT14.070" = @{ data_type="FLOAT";   unit="K" }               # Temperature difference
    "DPT14.072" = @{ data_type="FLOAT";   unit="W/(mK)" }          # Thermal Conductivity
    "DPT14.074" = @{ data_type="FLOAT";   unit="s" }               # Time
    "DPT14.075" = @{ data_type="FLOAT";   unit="Nm" }              # Torque
    "DPT14.076" = @{ data_type="FLOAT";   unit=("m" + $sup3) }     # Volume
    "DPT14.077" = @{ data_type="FLOAT";   unit=("m" + $sup3 + "/s") } # Volume Flux
    "DPT14.078" = @{ data_type="FLOAT";   unit="N" }               # Weight
    "DPT14.079" = @{ data_type="FLOAT";   unit="J" }               # Work
    "DPT14.080" = @{ data_type="FLOAT";   unit="VA" }              # Apparent Power
    # DPT 18 — Szenen
    "DPT18.001" = @{ data_type="INTEGER"; unit="" }
    # DPT 20 — 1-Byte Enum/Modi (vollständig nach KNX-Spec)
    "DPT20.001" = @{ data_type="INTEGER"; unit="" }  # SCLO Mode
    "DPT20.002" = @{ data_type="INTEGER"; unit="" }  # Building Mode
    "DPT20.003" = @{ data_type="INTEGER"; unit="" }  # Occupancy Mode
    "DPT20.004" = @{ data_type="INTEGER"; unit="" }  # Priority
    "DPT20.005" = @{ data_type="INTEGER"; unit="" }  # Light Application Mode
    "DPT20.006" = @{ data_type="INTEGER"; unit="" }  # Application Area
    "DPT20.007" = @{ data_type="INTEGER"; unit="" }  # Alarm Class Type
    "DPT20.008" = @{ data_type="INTEGER"; unit="" }  # PSU Mode
    "DPT20.011" = @{ data_type="INTEGER"; unit="" }  # Error Class System
    "DPT20.012" = @{ data_type="INTEGER"; unit="" }  # Error Class HVAC
    "DPT20.013" = @{ data_type="INTEGER"; unit="" }  # Time Delay
    "DPT20.014" = @{ data_type="INTEGER"; unit="" }  # Beaufort Wind Scale
    "DPT20.017" = @{ data_type="INTEGER"; unit="" }  # Sensor Select
    "DPT20.020" = @{ data_type="INTEGER"; unit="" }  # Actuator Connect Type
    "DPT20.021" = @{ data_type="INTEGER"; unit="" }  # Cloud Cover
    "DPT20.100" = @{ data_type="INTEGER"; unit="" }  # Fuel Type
    "DPT20.101" = @{ data_type="INTEGER"; unit="" }  # Burner Type
    "DPT20.102" = @{ data_type="INTEGER"; unit="" }  # HVAC Operating Mode
    "DPT20.103" = @{ data_type="INTEGER"; unit="" }  # DHW Mode
    "DPT20.104" = @{ data_type="INTEGER"; unit="" }  # Load Priority
    "DPT20.105" = @{ data_type="INTEGER"; unit="" }  # HVAC Controller Mode
    "DPT20.106" = @{ data_type="INTEGER"; unit="" }  # HVAC Emergency Mode
    "DPT20.107" = @{ data_type="INTEGER"; unit="" }  # Changeover Mode
    "DPT20.108" = @{ data_type="INTEGER"; unit="" }  # Valve Mode
    "DPT20.109" = @{ data_type="INTEGER"; unit="" }  # Damper Mode
    "DPT20.110" = @{ data_type="INTEGER"; unit="" }  # Heater Mode
    "DPT20.111" = @{ data_type="INTEGER"; unit="" }  # Fan Mode
    "DPT20.112" = @{ data_type="INTEGER"; unit="" }  # Master/Slave Mode
    "DPT20.113" = @{ data_type="INTEGER"; unit="" }  # Status Room Setpoint
    "DPT20.600" = @{ data_type="INTEGER"; unit="" }  # Behaviour Lock/Unlock
    "DPT20.601" = @{ data_type="INTEGER"; unit="" }  # Behaviour Bus PowerUp
    "DPT20.602" = @{ data_type="INTEGER"; unit="" }  # DALI Fade Time
    "DPT20.603" = @{ data_type="INTEGER"; unit="" }  # Blinking Mode
    "DPT20.604" = @{ data_type="INTEGER"; unit="" }  # Light Control Mode
    # DPT 29 — 64-Bit signed (Smart Metering)
    "DPT29.010" = @{ data_type="INTEGER"; unit="Wh" }   # Active Energy
    "DPT29.011" = @{ data_type="INTEGER"; unit="VAh" }  # Apparent Energy
    "DPT29.012" = @{ data_type="INTEGER"; unit="VARh" } # Reactive Energy
}

# Standard-Subtyp je Haupttyp (wenn ETS nur "DPT-9" liefert)
$DptMainDefaults = @{
    1=  "001"; 2="001"; 3="007"; 5="001"; 6="010"; 7="001"; 8="001"; 9="001"
    10= "001"; 11="001"; 12="001"; 13="001"; 14="056"; 16="000"; 18="001"; 19="001"; 20="102"; 29="010"
}

function Convert-EtsDpt {
    param([string]$raw)
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }

    # DPST-X-Y  → DPT{X}.{Y:D3}
    if ($raw -match '^DPST-(\d+)-(\d+)$') {
        $main = $Matches[1]
        $sub  = $Matches[2].PadLeft(3,'0')
        return "DPT${main}.${sub}"
    }
    # DPT-X  → DPT{X}.{default}
    if ($raw -match '^DPT-(\d+)$') {
        $main = [int]$Matches[1]
        $sub  = if ($DptMainDefaults.ContainsKey($main)) { $DptMainDefaults[$main] } else { "001" }
        return "DPT${main}.${sub}"
    }
    # Bereits im openTWS-Format (DPT9.001)?
    if ($raw -match '^DPT\d+\.\d+$') { return $raw }

    return $null
}

# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
function Invoke-Api {
    param(
        [string] $Method,
        [string] $Path,
        [hashtable] $Body = $null
    )
    $uri     = "$($Url.TrimEnd('/'))/api/v1$Path"
    $headers = @{ "X-API-Key" = $ApiKey; "Content-Type" = "application/json; charset=utf-8" }
    $params  = @{ Method=$Method; Uri=$uri; Headers=$headers; UseBasicParsing=$true }
    if ($null -ne $Body) {
        # Explizit als UTF-8-Bytes senden — verhindert Kodierungsfehler bei Sonderzeichen
        # wenn die CSV mit -Encoding Default (ANSI) gelesen wurde
        $json = $Body | ConvertTo-Json -Depth 10 -Compress
        $params["Body"] = [System.Text.Encoding]::UTF8.GetBytes($json)
    }
    $resp = Invoke-WebRequest @params
    return ($resp.Content | ConvertFrom-Json)
}

function Find-AdapterByName {
    param([string]$Name)
    $instances = Invoke-Api -Method GET -Path "/adapters/instances"
    foreach ($inst in $instances) {
        if ($inst.name -eq $Name) { return $inst.id }
    }
    throw "KNX-Adapter-Instanz '$Name' nicht gefunden. Verfügbar: $(($instances | ForEach-Object { $_.name }) -join ', ')"
}

function Detect-Encoding {
    param([string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    # UTF-8 BOM: EF BB BF
    if ($bytes.Count -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        return "UTF8"
    }
    # UTF-16 LE BOM: FF FE
    if ($bytes.Count -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) {
        return "Unicode"
    }
    # Kein BOM → ETS 5 ANSI/Windows-1252
    return "Default"
}

function Detect-Delimiter {
    param([string]$Path, [string]$Enc)
    $firstLine = Get-Content -Path $Path -TotalCount 1 -Encoding $Enc
    # @() erzwingt Array → .Count auch bei 0 oder 1 Treffer gültig (StrictMode-sicher)
    $semis  = @($firstLine.ToCharArray() | Where-Object { $_ -eq ';' }).Count
    $commas = @($firstLine.ToCharArray() | Where-Object { $_ -eq ',' }).Count
    if ($semis -ge $commas) { return ';' } else { return ',' }
}

# Erkennt, welche Spalte eine GA enthält (verschiedene ETS-Sprachversionen)
$AddressCols = @("Address","Adresse","Gruppenadresse")
$NameCols    = @("Group name","Gruppenname","Name","Description","Beschreibung")
$DptCols     = @("DatapointType","Datenpunkttyp","DPT","Datentyp")

function Get-ColValue {
    param($Row, [string[]]$Candidates)
    foreach ($c in $Candidates) {
        if ($Row.PSObject.Properties.Name -contains $c) {
            $v = $Row.$c
            if (-not [string]::IsNullOrWhiteSpace($v)) { return $v.Trim('"') }
        }
    }
    return $null
}

# ---------------------------------------------------------------------------
# Hauptlogik
# ---------------------------------------------------------------------------

Write-Host "openTWS ETS-GA-Import gestartet" -ForegroundColor Cyan
Write-Host "  Datei    : $File"
Write-Host "  Kodierung: $Encoding (Auto = BOM-Erkennung: UTF8 oder Default/ANSI)"
Write-Host "  Adapter  : $Adapter"
Write-Host "  Richtung : $Direction"
Write-Host ""

# CSV einlesen
if (-not (Test-Path $File)) { throw "Datei nicht gefunden: $File" }
if ($Encoding -eq "Auto") {
    $Encoding = Detect-Encoding -Path $File
    Write-Host "  Kodierung: Auto $arr $Encoding erkannt" -ForegroundColor Gray
}
$delim   = Detect-Delimiter -Path $File -Enc $Encoding
$rows    = Import-Csv -Path $File -Delimiter $delim -Encoding $Encoding

# Adapter-ID ermitteln
Write-Host "Suche Adapter-Instanz '$Adapter'..." -ForegroundColor Yellow
$adapterId = Find-AdapterByName -Name $Adapter
Write-Host "  $arr ID: $adapterId" -ForegroundColor Green

# Statistik
$ok      = 0
$skipped = 0
$failed  = [System.Collections.Generic.List[string]]::new()

$total = @($rows).Count
$i     = 0

foreach ($row in $rows) {
    $i++
    $ga   = Get-ColValue $row $AddressCols
    $name = Get-ColValue $row $NameCols
    $dptRaw = Get-ColValue $row $DptCols

    # GA fehlt oder ist unvollständig (z.B. "2/7/-") → überspringen
    if ([string]::IsNullOrWhiteSpace($ga)) { $skipped++; continue }
    if ($ga -notmatch '^\d+/\d+/\d+$')    { $skipped++; continue }
    if ([string]::IsNullOrWhiteSpace($name)) { $name = $ga }

    Write-Progress -Activity "Importiere GAs" -Status "$i/$total  $ga  $name" -PercentComplete ([int]($i * 100 / $total))

    try {
        # DPT auflösen
        $dptId    = Convert-EtsDpt -raw $dptRaw
        $dataType = "FLOAT"
        $unit     = ""

        if ($null -ne $dptId -and $DptMap.ContainsKey($dptId)) {
            $dataType = $DptMap[$dptId].data_type
            $unit     = $DptMap[$dptId].unit
        }

        # DataPoint anlegen
        $dpBody = @{ name=$name; data_type=$dataType }
        if (-not [string]::IsNullOrEmpty($unit)) { $dpBody["unit"] = $unit }

        $dp = Invoke-Api -Method POST -Path "/datapoints" -Body $dpBody

        # Binding anlegen
        $bindConfig = @{ group_address=$ga }
        if ($null -ne $dptId) { $bindConfig["dpt_id"] = $dptId }

        $bindBody = @{
            adapter_instance_id = $adapterId
            direction           = $Direction
            config              = $bindConfig
        }
        Invoke-Api -Method POST -Path "/datapoints/$($dp.id)/bindings" -Body $bindBody | Out-Null

        $ok++
        Write-Verbose "OK  $ga  →  DP $($dp.id)"
    }
    catch {
        $msg = "FEHLER  $ga  ($name): $_"
        $failed.Add($msg)
        Write-Warning $msg
    }
}

Write-Progress -Activity "Importiere GAs" -Completed

# ---------------------------------------------------------------------------
# Ergebnis
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Import abgeschlossen" -ForegroundColor Cyan
Write-Host "  Erfolgreich : $ok"
Write-Host "  Übersprungen: $skipped  (keine oder unvollständige Adresse)"
Write-Host "  Fehler      : $($failed.Count)"

if ($failed.Count -gt 0) {
    if (-not [string]::IsNullOrWhiteSpace($LogFile)) {
        $failed | Set-Content -Path $LogFile -Encoding UTF8
        Write-Host "  Fehlerprotokoll: $LogFile" -ForegroundColor Yellow
    }
    else {
        Write-Host ""
        Write-Host "Fehlgeschlagene Gruppenadressen:" -ForegroundColor Yellow
        $failed | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    }
}
