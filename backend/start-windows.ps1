$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
if (-not (Test-Path '.venv')) { py -3 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
if (-not $env:BR_TOKEN) {
    $env:BR_TOKEN = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
}
if (-not $env:IB_PORT) { $env:IB_PORT = '7497' }
Write-Host 'Dashboard: http://127.0.0.1:8787/#br'
Write-Host 'Clave privada del servicio (no es tu contraseña de IBKR):'
Write-Host $env:BR_TOKEN
Write-Host 'Abre el dashboard, pulsa Conectar IBKR y pega esta clave. Mantén TWS y esta ventana abiertos.'
& .\.venv\Scripts\python.exe -m uvicorn backend.service:app --host 127.0.0.1 --port 8787 --workers 1
