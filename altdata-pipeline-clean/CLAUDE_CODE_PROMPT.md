# Prompt para Claude Code — SysFund AltData Pipeline

Copia el bloque de abajo (entre `--- BEGIN ---` y `--- END ---`) y pégalo
en la CLI de Claude Code, parado en la raíz del proyecto descomprimido.

--- BEGIN ---

Sos un senior software engineer ayudándome a levantar un alternative data
pipeline para un systematic fund en Chile. El proyecto ya está
descomprimido en este directorio.

## Contexto del proyecto
- Scrapea CMF (hechos esenciales), BCCh (macro), Portal Inmobiliario (RE)
- Usa Claude API (Haiku/Sonnet) para extraer señales de trading
- DuckDB local + FastAPI en puerto 8003 + dashboard React standalone

## Pre-requisitos que debo tener listos antes
1. Python 3.10+ (3.11 recomendado; el código funciona desde 3.10)
2. ANTHROPIC_API_KEY válida
3. Cuenta BCCh: https://si3.bcentral.cl/siete/ (gratis, 5 min)
   -> me da BCCH_USER y BCCH_PASS
4. (Opcional) Bloomberg Terminal abierto en localhost:8194

## Tu plan de ejecución
1. Verificar Python >= 3.10
2. `bash scripts/setup.sh` (crea venv, instala deps, instala Chromium
   para Playwright, copia .env.template -> .env)
3. Pedirme que edite `.env` con mis credenciales (Anthropic + BCCh).
   IMPORTANTE: dejar DEMO_MODE=false para que real estate scrapee real.
4. Activar el venv: `source .venv/bin/activate`
5. `python main.py --test` -> reportar qué conexiones quedaron OK
   - anthropic OK -> NLP listo
   - bcch OK -> macro listo
   - duckdb OK -> storage listo
   - bloomberg en WARN está bien si no tengo Terminal
6. Si todo OK, primera corrida real: `python main.py --once`
   - Esto trae los hechos esenciales del día desde CMF, los pasa por
     Claude para sacar señales, baja series macro del BCCh, scrapea
     200 propiedades del Portal Inmobiliario, las scorea vs el mercado.
7. Levantar API: `python main.py --api-only` en otra terminal
8. Curl de smoke: `curl http://localhost:8003/signals`
9. Abrir el dashboard React: `dashboard/index.html` en el browser
10. Modo producción (scheduler + API en un mismo proceso): `python main.py`

## Reglas
- Si un import falla, revisar requirements.txt
- Bloomberg es opcional - si falla, continuar sin él
- Si Portal Inmobiliario bloquea con captcha, el código ya hace fallback
  a demo data automáticamente (lo verás en los logs como "RE demo data")
- Si CMF cambia el HTML del portal y el scraper devuelve 0 hechos,
  revisar selectores en scrapers/cmf_scraper.py (línea ~70 onwards)
- NO commitear el .env

## Si algo se rompe
- Logs detallados en `data/pipeline.log`
- Para resetear la DB: borrar `data/altdata.duckdb*`
- Para correr solo un módulo: importar la clase en una REPL Python

Empezá con el paso 1.

--- END ---

