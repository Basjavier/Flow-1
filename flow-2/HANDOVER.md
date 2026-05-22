# HANDOVER — flow-2

Estado del proyecto al cierre de la sesión de scaffolding.

## Estado actual (Sprint 0 — esqueleto corrible)

Lo que **ya funciona**, 100% offline:

- Motor de scoring (`backend/app/scoring/engine.py`) que evalúa reglas declarativas.
- Reglas iniciales (`backend/app/scoring/rules.yml`): 5 rojas, 4 amarillas.
- CLI `dd_full.py`: toma un JSON de propiedad, imprime el veredicto en terminal y
  genera un reporte HTML.
- 3 propiedades de ejemplo con veredictos verificados:
  - `las_condes_verde.json` → **VERDE**, sin observaciones.
  - `maipu_rojo_remate.json` → **ROJO** (remate + embargo + hipoteca).
  - `san_bernardo_rojo_defuncion.json` → **ROJO** (propietario fallecido).
- Suite de tests: **18 tests pasando** (`pytest backend/tests`).

## Lo que NO existe todavía (a diferencia del KICKOFF original)

El documento KICKOFF describía un sistema más grande (`saas-dd-mvp`) con scrapers
reales, captura con Playwright y parsers LLM. **Nada de eso está implementado acá.**
flow-2 arranca como el esqueleto mínimo y honesto sobre el que construir.

No hay: scrapers (SII, IDE Chile, Registro Civil, Diario Oficial), captura de
fixtures reales, parsers LLM de CIP/escrituras, backend web/API, ni base de datos.

## Próximos pasos sugeridos

1. **Conectar fuentes reales una por una**, siempre con modo fixture primero:
   capturar una respuesta real, guardarla como fixture, escribir el scraper contra
   el fixture, y recién después apuntar a producción.
   - SII (rol → avalúo, destino).
   - IDE Chile / Geoportal MINVU (capas de zonificación → datos del CIP).
   - Registro Civil (propietario vivo/fallecido).
   - Diario Oficial (avisos de remate; ojo: la respuesta es HTML, no JSON).
2. **Parser LLM del CIP** (PDF → JSON), iterando el prompt contra CIPs reales hasta
   >90% de accuracy en rol, zona, altura máxima y coeficiente de ocupación.
   Acá entra `ANTHROPIC_API_KEY`.
3. **Afinar `rules.yml`** contra 2-3 propiedades reales del portafolio de Javier,
   comparando contra su DD manual.

## Notas técnicas

- `output/` (reportes HTML generados) está en `.gitignore`.
- `conftest.py` en la raíz de flow-2 agrega el directorio al `sys.path` para que
  `import backend...` funcione al correr pytest.
- Python 3.11+.
