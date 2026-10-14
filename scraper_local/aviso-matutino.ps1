param([switch]$Simular)
# Cartel a pantalla completa (9h, Task Scheduler) que resume corridas con problemas
# desde la ultima vez que se miro. Lee logs\resultados\*.json (los escribe run.py).
# -Simular: muestra un cartel de ejemplo sin tocar el marcador (verificacion visual).
$ErrorActionPreference = 'Stop'
$dirResultados = Join-Path $PSScriptRoot "logs\resultados"
$marcador = Join-Path $dirResultados ".ultimo-visto"

function Show-Cartel {
    param([string[]]$Lineas)
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $form = New-Object System.Windows.Forms.Form
    $form.FormBorderStyle = 'None'
    $form.WindowState = 'Maximized'
    $form.TopMost = $true
    $form.KeyPreview = $true
    $form.BackColor = [System.Drawing.Color]::FromArgb(140, 20, 20)

    $titulo = New-Object System.Windows.Forms.Label
    $titulo.Text = "Boca de Urna: corrida con problemas"
    $titulo.ForeColor = [System.Drawing.Color]::White
    $titulo.Font = New-Object System.Drawing.Font("Segoe UI", 34, [System.Drawing.FontStyle]::Bold)
    $titulo.TextAlign = 'MiddleCenter'
    $titulo.Dock = 'Top'
    $titulo.Height = 170

    $cuerpo = New-Object System.Windows.Forms.Label
    $cuerpo.Text = ($Lineas -join "`r`n`r`n")
    $cuerpo.ForeColor = [System.Drawing.Color]::White
    $cuerpo.Font = New-Object System.Drawing.Font("Segoe UI", 20)
    $cuerpo.TextAlign = 'MiddleCenter'
    $cuerpo.Dock = 'Fill'

    $boton = New-Object System.Windows.Forms.Button
    $boton.Text = "Entendido"
    $boton.Font = New-Object System.Drawing.Font("Segoe UI", 20, [System.Drawing.FontStyle]::Bold)
    $boton.ForeColor = [System.Drawing.Color]::FromArgb(140, 20, 20)
    $boton.BackColor = [System.Drawing.Color]::White
    $boton.Dock = 'Bottom'
    $boton.Height = 110
    $boton.Add_Click({ $form.Close() })

    $form.Add_KeyDown({ if ($_.KeyCode -eq 'Escape') { $form.Close() } })
    $form.Controls.Add($cuerpo)
    $form.Controls.Add($titulo)
    $form.Controls.Add($boton)
    $form.Add_Shown({ $form.Activate() })
    [void]$form.ShowDialog()
}

if ($Simular) {
    Show-Cartel -Lineas @("13/10 16h - Sin posts en: tiktok (captcha / cuenta quemada). 19 avisos en la corrida.")
    return
}

$ultimoVisto = [datetime]::new(1970, 1, 1, 0, 0, 0, [DateTimeKind]::Utc)
if (Test-Path $marcador) {
    try { $ultimoVisto = ([datetime]((Get-Content $marcador -Raw).Trim())).ToUniversalTime() } catch { }  # marcador corrupto -> queda en epoch, reaparecen las alertas (falla segura)
}

$ahora = (Get-Date).ToUniversalTime()
$lineas = New-Object System.Collections.Generic.List[string]
$huboCorridaReciente = $false

if (Test-Path $dirResultados) {
    foreach ($a in (Get-ChildItem -Path $dirResultados -Filter *.json -ErrorAction SilentlyContinue)) {
        try { $r = Get-Content $a.FullName -Raw | ConvertFrom-Json } catch { continue }
        if (-not $r.fin_utc) { continue }
        $fin = ([datetime]$r.fin_utc).ToUniversalTime()
        if ($fin -gt $ahora.AddHours(-20)) { $huboCorridaReciente = $true }
        if ($r.nivel -eq 'alerta' -and $fin -gt $ultimoVisto) {
            $cuando = $fin.ToLocalTime().ToString("dd/MM HH'h'")
            $lineas.Add("$cuando - $($r.mensaje)")
        }
    }
}

if (-not $huboCorridaReciente) {
    $lineas.Add("No hubo ninguna corrida registrada en las ultimas 20 h (PC apagada o la tarea no disparo).")
}

if ($lineas.Count -eq 0) { return }   # todo bien: no molestar

Show-Cartel -Lineas $lineas
# Marcar "visto" SOLO despues de cerrar el cartel (si se mata el script, reaparece)
if (-not (Test-Path $dirResultados)) { New-Item -ItemType Directory -Path $dirResultados -Force | Out-Null }
$ahora.ToString("o") | Set-Content -Path $marcador -Encoding utf8
