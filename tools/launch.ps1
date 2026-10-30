$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    python -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 ou plus récent est requis.' }
}
& $pythonPath -c "import streamlit, openpyxl, docx, pypdf, xlrd, reportlab; from packaging.version import Version; assert Version('1.50') <= Version(streamlit.__version__) < Version('2')" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $pythonPath -m pip install --isolated --index-url https://pypi.org/simple -r (Join-Path $projectRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Installation impossible. Vérifier la connexion Internet.' }
}
$appPort = if ($env:IMPACT_PORT) { $env:IMPACT_PORT } else { '8765' }
Write-Host "IMPACT utilise Python : $pythonPath"
Start-Process "http://127.0.0.1:$appPort"
& $pythonPath -m streamlit run (Join-Path $projectRoot 'app.py') --server.address 127.0.0.1 --server.port $appPort --server.headless true
