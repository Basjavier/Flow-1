# CLAUDE.md — flow-2

Memoria del proyecto para sesiones de Claude Code. Leelo completo al empezar.

## Qué es esto

**flow-2** es una herramienta de **Due Diligence inmobiliaria** para Chile.
A partir de los datos de una propiedad (rol, comuna, propietario, CIP, gravámenes,
estado en remate, etc.) genera un veredicto **VERDE / AMARILLO / ROJO** con las
observaciones que lo justifican, y un reporte HTML.

Es un proyecto **separado** del analizador de remates que vive en la raíz del repo
(`../main.py`, `../remates/`). No los mezcles.

El objetivo de negocio: pasar de una DD manual de 3-7 días a un reporte preliminar
en ~30 minutos, codificando el criterio experto en reglas editables.

## Estructura

```
flow-2/
  backend/app/scoring/engine.py   Motor de scoring (evalúa reglas sobre la propiedad)
  backend/app/scoring/rules.yml   Reglas declarativas — ACÁ se codifica el criterio
  backend/app/scrapers/           Scrapers de las 4 fuentes (modo fixture / real)
  backend/app/scrapers/fixtures/  Respuestas guardadas por fuente (offline)
  backend/app/scrapers/config/    layers.yml — capas WFS de IDE Chile (placeholders)
  backend/app/enrich.py           Seed -> consulta scrapers -> JSON de propiedad
  backend/tests/                  Tests offline (pytest)
  standalone-tools/dd_full.py     CLI: JSON de propiedad -> reporte HTML
  standalone-tools/ejemplos/      Propiedades completas + seeds para --enrich
  scripts/setup.ps1               Bootstrap en Windows
  .env.example                    Plantilla de variables de entorno
```

## Cómo correr

```bash
# Tests (offline, sin red)
cd flow-2 && python -m pytest backend/tests -q

# Una DD de ejemplo (JSON de propiedad completo)
cd flow-2/standalone-tools
python dd_full.py ejemplos/las_condes_verde.json --abrir

# DD desde un seed: completa los datos con los scrapers y después evalúa.
# En modo fixture (default) usa las respuestas guardadas; offline.
python dd_full.py ejemplos/seed_las_condes.json --enrich
```

## Modelo de scoring

- `rules.yml` es una lista de reglas. Cada regla mira un campo del JSON de la
  propiedad (ruta con puntos, ej. `conservador.embargos`), aplica un operador y,
  si se cumple, agrega una observación con su severidad.
- Operadores: `is_true`, `is_false`, `equals`, `not_equals`, `exists_nonempty`,
  `is_empty`, `gt`, `lt`, `gte`, `lte`, `in`, `contains`.
- Severidades: `rojo` (bloquea) y `amarillo` (precaución).
- Score final: **ROJO** si hay alguna roja; **AMARILLO** si hay alguna amarilla y
  ninguna roja; **VERDE** si no se gatilla ninguna.
- Para ajustar el criterio: editá `rules.yml`. No hace falta tocar `engine.py`
  salvo que necesites un operador nuevo.

## Esquema del JSON de propiedad

Campos que las reglas actuales esperan (todos opcionales; lo ausente no gatilla):
`direccion`, `comuna`, `rol`, `propietario.{nombre,rut}`,
`sii.{avaluo_fiscal_uf,destino}`,
`cip.{zona,altura_maxima_m,coef_ocupacion_suelo,coef_constructibilidad,uso_permitido}`,
`registro_civil.propietario_vivo`,
`conservador.{hipotecas,prohibiciones,embargos,litigios}` (listas),
`remate.en_remate`, `diario_oficial.publicaciones`.
Ver `standalone-tools/ejemplos/` para el formato completo.

## Decisiones tomadas (no revertir sin avisar)

- **Reglas declarativas en YAML**, no hardcodeadas en Python. El criterio experto
  vive en `rules.yml` para que Javier lo edite sin tocar código.
- **Campo ausente nunca gatilla una observación**. Ausencia de dato ≠ riesgo
  confirmado. (Excepción intencional: `sin_cip` gatilla amarillo cuando falta la zona.)
- **Dependencias acotadas**: `pyyaml`, `requests`, `beautifulsoup4` (runtime) y
  `pytest` (dev). El reporte HTML se arma con f-strings, sin motor de templates.
  `requests` solo se importa en el modo real de los scrapers.
- **Tests 100% offline**. Los scrapers corren en modo fixture por default
  (`SCRAPER_FIXTURE_MODE=1`); ningún test toca la red.

## Scrapers / fuentes (fixture-first)

- 4 fuentes, cada una un scraper en `backend/app/scrapers/` que hereda de
  `BaseScraper` y opera en dos modos según `SCRAPER_FIXTURE_MODE`:
  - **fixture** (default): lee `fixtures/<fuente>/<clave>.{json,html}`. Offline.
  - **real** (`=0`): pega contra producción. Se corre desde una máquina con
    acceso a las fuentes chilenas.
- **IDE Chile** (`ide_chile.py`): WFS estándar, automatizable. El modo real arma
  un GetFeature con filtro `INTERSECTS` por lat/lon y parsea GeoJSON. Los
  typenames/campos en `config/layers.yml` son **placeholders**: validarlos contra
  el GetCapabilities real antes de producción.
- **Diario Oficial** (`diario_oficial.py`): respuesta HTML (no JSON), parser con
  BeautifulSoup. El selector está escrito contra la estructura esperada del
  buscador; **validar contra HTML real** y ajustar `BUSCADOR_URL`/selectores.
- **SII / Registro Civil / Conservador**: captura asistida (captcha/login/pago).
  El modo real lanza `AssistedCaptureRequired` con instrucciones; el dato se
  carga vía fixture capturado a mano. Ver el docstring de cada módulo.
- `enrich.py` toma un *seed* (rol, comuna, propietario.rut, lat, lon) y completa
  el JSON de propiedad consultando cada scraper. Aísla cada fuente: si falla o
  necesita captura, lo registra en `propiedad["_fuentes"]` y la DD sigue. Deriva
  `remate.en_remate=True` si el Diario Oficial trae un aviso de remate.

## Cómo trabaja Javier

- Responder en **español**.
- Respuestas cortas: **sin bullets ni encabezados decorativos**. Directo.
- Para tests usar **modo fixture/offline**; modo real solo si lo pide explícito.
- Mostrar el diff de lo que se cambió cuando se le pida.
- Si algo es un placeholder, no tratarlo como código de producción.
