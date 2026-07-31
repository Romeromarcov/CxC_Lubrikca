# Monitor de logs de Railway (CxC_Lubrikca, entorno Staging).
#
# Pensado para correr como Tarea Programada de Windows cada 5 minutos.
# Independiente de cualquier sesion de Claude Code: revisa los ultimos logs,
# busca patrones de error, y si encuentra algo:
#   1. Lo agrega (con timestamp) a railway_monitor_findings.log en la raiz
#      del repo -- historial consultable aunque se pierda la notificacion.
#   2. Muestra un aviso nativo de Windows (globo/balloon tip).
#
# Si Claude Code no esta corriendo en ese momento, el usuario puede abrir
# una sesion despues, pegar el contenido de railway_monitor_findings.log,
# y pedir que se revise/corrija -- el archivo queda como registro durable.

param(
    [string]$Service = "CxC_Lubrikca",
    [string]$Since = "6m"
)

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogFile = Join-Path $RepoRoot "railway_monitor_findings.log"
$ErrorPatterns = 'Error|Exception|Traceback|AttributeError|TypeError|KeyError|CRITICAL|500 Internal|Fallo captura|Failed'

Set-Location $RepoRoot

try {
    $raw = & railway logs --service $Service --since $Since --lines 500 2>&1
} catch {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LogFile -Value "`n===== $timestamp (fallo al consultar Railway) =====`n$($_.Exception.Message)"
    exit 1
}

$found = $raw | Select-String -Pattern $ErrorPatterns

if ($found) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $entry = "`n===== $timestamp -- $($found.Count) linea(s) con error =====`n" + (($found | ForEach-Object { $_.Line }) -join "`n")
    Add-Content -Path $LogFile -Value $entry

    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $notify = New-Object System.Windows.Forms.NotifyIcon
    $notify.Icon = [System.Drawing.SystemIcons]::Warning
    $notify.Visible = $true
    $notify.BalloonTipTitle = "CxC_Lubrikca -- error en Railway (Staging)"
    $notify.BalloonTipText = "Se detectaron $($found.Count) linea(s) con error. Detalle en railway_monitor_findings.log"
    $notify.ShowBalloonTip(15000)
    Start-Sleep -Seconds 16
    $notify.Dispose()
}
