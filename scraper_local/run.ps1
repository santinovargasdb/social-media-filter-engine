# Corrida del scraper local de la Boca de Urna (la dispara Task Scheduler).
# El exit code de run.py se propaga para que el Task Scheduler registre fallos.
Set-Location $PSScriptRoot
& "$PSScriptRoot\.venv\Scripts\python.exe" run.py
exit $LASTEXITCODE
