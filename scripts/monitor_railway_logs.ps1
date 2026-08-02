# Monitor de salud de Staging (CxC_Lubrikca): logs de Railway (app + Postgres)
# + salud directa de Postgres + comparacion Sheets vs Postgres.
#
# Pensado para correr como Tarea Programada de Windows. Independiente de
# cualquier sesion de Claude Code: revisa todo, y si encuentra algo:
#   1. Lo agrega (con timestamp) a railway_monitor_findings.log en la raiz
#      del repo -- historial consultable aunque se pierda la notificacion.
#   2. Muestra un aviso nativo de Windows (globo/balloon tip).
#
# Si Claude Code no esta corriendo en ese momento, el usuario puede abrir
# una sesion despues, pegar el contenido de railway_monitor_findings.log,
# y pedir que se revise/corrija -- el archivo queda como registro durable.

param(
    [string]$AppService = "CxC_Lubrikca",
    [string]$DbService = "Postgres",
    [string]$Since = "190m",
    [string]$DatabaseUrl = "postgresql://postgres:gcGQsbzXyxjHAfnhrlPUgojVALCSBKZD@sakura.proxy.rlwy.net:59119/railway",
    [string]$GoogleServiceAccountFile = "secrets\project-21467edd-9ee5-4503-a83-f593df3a3a7c.json"
)

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogFile = Join-Path $RepoRoot "railway_monitor_findings.log"
$ErrorPatterns = 'Error|Exception|Traceback|AttributeError|TypeError|KeyError|CRITICAL|500 Internal|Fallo captura|Failed'
# Ruido conocido del propio CLI de Railway (glitches de red al traer logs,
# no errores de la app) -- se descarta antes de aplicar $ErrorPatterns.
$CliNoisePatterns = 'Failed to fetch|error decoding response body'

Set-Location $RepoRoot
$hallazgos = @()

# --- 1. Logs de los servicios de Railway (app + Postgres) -------------------
foreach ($svc in @($AppService, $DbService)) {
    try {
        $raw = & railway logs --service $svc --since $Since --lines 500 2>&1
        $found = $raw | Where-Object { $_ -notmatch $CliNoisePatterns } | Select-String -Pattern $ErrorPatterns
        if ($found) {
            $hallazgos += "--- Logs de '$svc' ($($found.Count) linea(s)) ---"
            $hallazgos += ($found | ForEach-Object { $_.Line })
        }
    } catch {
        $hallazgos += "--- No se pudieron leer logs de '$svc': $($_.Exception.Message) ---"
    }
}

# --- 2. Salud de Postgres + comparacion Sheets vs Postgres -------------------
$env:GOOGLE_SERVICE_ACCOUNT_FILE = Join-Path $RepoRoot $GoogleServiceAccountFile
$healthOutput = & python (Join-Path $PSScriptRoot "monitor_staging_health.py") --database-url $DatabaseUrl 2>&1
$healthExit = $LASTEXITCODE
if ($healthExit -ne 0) {
    $hallazgos += "--- monitor_staging_health.py (exit $healthExit) ---"
    $hallazgos += $healthOutput
}

# --- Reporte --------------------------------------------------------------
if ($hallazgos.Count -gt 0) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $entry = "`n===== $timestamp =====`n" + ($hallazgos -join "`n")
    Add-Content -Path $LogFile -Value $entry

    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $notify = New-Object System.Windows.Forms.NotifyIcon
    $notify.Icon = [System.Drawing.SystemIcons]::Warning
    $notify.Visible = $true
    $notify.BalloonTipTitle = "CxC_Lubrikca -- revisar Staging"
    $notify.BalloonTipText = "Se detectaron hallazgos en logs/Postgres/comparacion. Detalle en railway_monitor_findings.log"
    $notify.ShowBalloonTip(15000)
    Start-Sleep -Seconds 16
    $notify.Dispose()
}
