#!/bin/bash
# ─────────────────────────────────────────────────────
# SysFund AltData Pipeline — Setup Script
# Correr una vez desde la raíz del proyecto
# ─────────────────────────────────────────────────────

set -e

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   SysFund AltData Pipeline — Setup       ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# 1. Python version check
echo "→ Verificando Python..."
python3 --version
if ! python3 -c "import sys; assert sys.version_info >= (3,11)" 2>/dev/null; then
    echo "⚠️  Se requiere Python 3.11+"
    exit 1
fi
echo "✅ Python OK"

# 2. Crear entorno virtual
echo "→ Creando entorno virtual..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip --quiet
echo "✅ Entorno virtual creado"

# 3. Instalar dependencias
echo "→ Instalando dependencias..."
pip install -r requirements.txt --quiet
echo "✅ Dependencias instaladas"

# 4. Playwright browsers
echo "→ Instalando Chromium para Playwright..."
playwright install chromium
echo "✅ Chromium instalado"

# 5. Crear .env desde template
if [ ! -f ".env" ]; then
    cp .env.template .env
    echo "✅ .env creado desde template"
    echo ""
    echo "⚠️  IMPORTANTE: edita .env con tus credenciales:"
    echo "   - ANTHROPIC_API_KEY"
    echo "   - BCCH_USER / BCCH_PASS (gratis en si3.bcentral.cl/siete/)"
    echo ""
else
    echo "✅ .env ya existe"
fi

# 6. Crear directorio de datos
mkdir -p data
echo "✅ Directorio data/ creado"

# 7. Bloomberg (opcional)
echo ""
echo "─────────────────────────────────────────────"
echo "Bloomberg Terminal (opcional):"
echo "  Si tienes Bloomberg abierto en localhost:8194,"
echo "  instala blpapi:"
echo "  pip install blpapi"
echo "─────────────────────────────────────────────"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Setup completo. Próximos pasos:        ║"
echo "║                                          ║"
echo "║   1. Editar .env con tus keys            ║"
echo "║   2. python main.py --test               ║"
echo "║   3. python main.py --demo               ║"
echo "║   4. python main.py (producción)         ║"
echo "╚══════════════════════════════════════════╝"
echo ""
