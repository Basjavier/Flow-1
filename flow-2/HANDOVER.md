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

- `scrapers/config/layers.yml`: los `typename` y nombres de campo del WFS son
  **candidatos sin validar**. La fuente real es el Geoportal Open Data MINVU
  (`ide.minvu.cl`), que es GeoNode y publica zonificación **por comuna** (el visor
  expone aliases tipo `MINVU::prc-las-condes-3`). Validar con `validar_ide.py`.
- `diario_oficial.py`: el `BUSCADOR_URL` y los selectores HTML son una aproximación.
  Validar contra un HTML real capturado del buscador.

## Restricción del entorno remoto

El entorno remoto (Claude Code on the web) tiene el **egress de red cerrado**:
DNS resuelve pero todo GET externo da 403 (probado contra `google.com` e
`ide.cl`), y WebFetch también. Por eso la validación contra producción **no se
puede hacer desde acá**; se corre desde tu PC con `standalone-tools/validar_ide.py`.

## Lo que NO existe todavía

No hay: parsers LLM de CIP/escrituras (PDF → JSON), backend web/API, ni base de
datos. La captura asistida (SII/Registro Civil/Conservador) es manual: el modo
real no resuelve captcha/login, solo indica qué fixture falta.

## Próximos pasos sugeridos

1. **Validar IDE Chile contra producción** (siguiente paso inmediato), desde tu PC:
   ```
   # Listar capas reales y encontrar el typename de la comuna:
   python standalone-tools/validar_ide.py --capabilities --url https://ide.minvu.cl/geoserver/ows
   # Consultar un punto, ver los campos y guardar el fixture real:
   python standalone-tools/validar_ide.py --typename <typename_real> --lat -33.4096 --lon -70.5681 --save
   ```
   Con esa salida: corregir `config/layers.yml` (`wfs_base_url`, `typename`,
   `geom_field`, mapa de `campos`) y el normalizador si hace falta. Después,
   misma idea con el Diario Oficial (capturar HTML real y ajustar selectores).
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
