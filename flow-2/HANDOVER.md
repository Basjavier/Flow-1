# HANDOVER — flow-2

Estado del proyecto al cierre de la sesión de scrapers (fixture-first).

## Estado actual (Sprint 1 — scrapers fixture-first)

Lo que **ya funciona**, 100% offline:

- Motor de scoring (`backend/app/scoring/engine.py`) que evalúa reglas declarativas.
- Reglas iniciales (`backend/app/scoring/rules.yml`): 5 rojas, 4 amarillas.
- CLI `dd_full.py`: toma un JSON de propiedad, imprime el veredicto en terminal y
  genera un reporte HTML. Con `--enrich` toma un *seed* y completa los datos con
  los scrapers antes de evaluar.
- **Scrapers de las 4 fuentes** (`backend/app/scrapers/`) en modo fixture/real:
  - IDE Chile (WFS GetFeature → GeoJSON → campos del CIP). Parser real.
  - Diario Oficial (HTML → avisos; detecta remates). Parser real con BeautifulSoup.
  - SII, Registro Civil, Conservador: captura asistida (modo real lanza
    `AssistedCaptureRequired` con instrucciones).
- `enrich.py`: seed → consulta scrapers → JSON de propiedad, con auditoría por
  fuente en `propiedad["_fuentes"]`.
- Ejemplos verificados:
  - JSON completos: `las_condes_verde`, `maipu_rojo_remate`, `san_bernardo_rojo_defuncion`.
  - Seeds para `--enrich`: `seed_las_condes.json` → **VERDE** (4 fuentes ok),
    `seed_maipu.json` → **ROJO** (remate derivado del Diario Oficial + embargo).
- Suite de tests: **33 tests pasando** (`pytest backend/tests`).

## Importante: fixtures vs. respuestas reales

Los scrapers están escritos contra la **estructura esperada** de cada fuente y
validados contra fixtures. Todavía **no se validaron contra respuestas reales**:

- `scrapers/config/layers.yml`: los `typename` y nombres de campo del WFS de IDE
  Chile son **placeholders**. Validar con `GetCapabilities` antes de producción.
- `diario_oficial.py`: el `BUSCADOR_URL` y los selectores HTML son una aproximación.
  Validar contra un HTML real capturado del buscador.

## Lo que NO existe todavía

No hay: parsers LLM de CIP/escrituras (PDF → JSON), backend web/API, ni base de
datos. La captura asistida (SII/Registro Civil/Conservador) es manual: el modo
real no resuelve captcha/login, solo indica qué fixture falta.

## Próximos pasos sugeridos

1. **Validar cada fuente contra producción**, desde una máquina con acceso
   (`SCRAPER_FIXTURE_MODE=0`): capturar una respuesta real, guardarla como fixture,
   y ajustar typenames/selectores donde haga falta.
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
