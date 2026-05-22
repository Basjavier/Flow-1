# Bootstrap de flow-2 (Due Diligence inmobiliaria).
# Uso: desde la carpeta flow-2, abrí PowerShell y corré  .\scripts\setup.ps1
#
# Si PowerShell bloquea el script:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Write-Host "== flow-2 setup ==" -ForegroundColor Cyan

# 1. Verificar Python 3.11+
$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { $python = $cmd; break }
}
if (-not $python) {
    Write-Host "No se encontró Python. Instalá Python 3.11+ desde https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}
$version = & $python --version
Write-Host "Python detectado: $version"

# 2. Crear virtualenv
$venv = Join-Path $root ".venv"
if (-not (Test-Path $venv)) {
    Write-Host "Creando virtualenv en .venv ..."
    & $python -m venv $venv
}
$venvPython = Join-Path $venv "Scripts\python.exe"

# 3. Instalar dependencias
Write-Host "Instalando dependencias ..."
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -r (Join-Path $root "requirements.txt")

# 4. Copiar .env.example a .env si no existe
$env = Join-Path $root ".env"
if (-not (Test-Path $env)) {
    Copy-Item (Join-Path $root ".env.example") $env
    Write-Host "Creado .env (editá tu ANTHROPIC_API_KEY)."
}

# 5. Correr los tests offline
Write-Host "Corriendo tests offline ..." -ForegroundColor Cyan
& $venvPython -m pytest (Join-Path $root "backend\tests") -q

Write-Host ""
Write-Host "Listo. Probá una DD de ejemplo:" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  cd standalone-tools"
Write-Host "  python dd_full.py ejemplos\las_condes_verde.json --abrir"
