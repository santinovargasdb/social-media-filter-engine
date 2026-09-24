param([string]$Redes = "")
# Corrida del scraper local de la Boca de Urna (la dispara Task Scheduler).
# Parámetro -Redes: lista separada por comas, p.ej. -Redes "twitter,tiktok".
# Si se omite, run.py usa las redes del config.json.
# El exit code de run.py se propaga para que el Task Scheduler registre fallos.
Set-Location $PSScriptRoot

# Variables de entorno: carga scraper_local\.env (en la PC de desarrollo cae al
# backend\.env, que ya tiene las keys). Formato KEY=VALOR por linea, # comenta.
# No pisa variables que ya vengan del entorno: lo que configure Task Scheduler
# o la sesion tiene prioridad sobre el archivo.
$envFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envFile)) { $envFile = Join-Path (Split-Path $PSScriptRoot) "backend\.env" }
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([^=#][^=]*)=(.*)$') {
            $name = $Matches[1].Trim(); $value = $Matches[2].Trim()
            if (-not (Test-Path "env:$name")) { Set-Item "env:$name" $value }
        }
    }
}

$argumentos = @("run.py")
if ($Redes) { $argumentos += @("--redes", $Redes) }
& "$PSScriptRoot\.venv\Scripts\python.exe" @argumentos
exit $LASTEXITCODE
