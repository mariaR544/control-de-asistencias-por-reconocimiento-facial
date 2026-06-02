# =====================================================================
#  Reconciliación de marcaciones desde la cámara Hikvision
#  Alternativa externa al job interno del backend (RECONCILE_ENABLED).
#
#  Llama al endpoint del backend que recupera del equipo los eventos
#  guardados e inserta los que falten (sin duplicar).
# =====================================================================
#
# Por defecto reconcilia los últimos días. Puedes pasar fechas como argumentos:
#   .\reconciliar.ps1 2026-06-01 2026-06-02
param(
    [string]$StartDate = "",
    [string]$EndDate = ""
)

# URL del backend (ajusta si corre en otra máquina/puerto)
$BackendUrl = "http://localhost:8000"

$query = @()
if ($StartDate -ne "") { $query += "start_date=$StartDate" }
if ($EndDate   -ne "") { $query += "end_date=$EndDate" }
$qs = if ($query.Count -gt 0) { "?" + ($query -join "&") } else { "" }

$url = "$BackendUrl/api/hikvision/pull-events$qs"

try {
    $resp = Invoke-RestMethod -Method Post -Uri $url -TimeoutSec 60
    Write-Output ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $resp.message)
} catch {
    Write-Error ("[{0}] Error al reconciliar: {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_.Exception.Message)
    exit 1
}

# =====================================================================
#  CÓMO REGISTRARLO EN EL PROGRAMADOR DE TAREAS DE WINDOWS
#  (ejecutar una vez en PowerShell como administrador):
#
#  $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
#      -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\reconciliar.ps1`""
#  $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
#      -RepetitionInterval (New-TimeSpan -Minutes 10)
#  Register-ScheduledTask -TaskName "ReconciliarAsistenciaGuayamuri" `
#      -Action $action -Trigger $trigger -Description "Recupera marcaciones de la cámara"
# =====================================================================
