# Real Estate Intelligence Agent — CLAUDE.md

Agente de análisis inmobiliario para la Región Metropolitana de Santiago.
Scraping → scoring compuesto → reportes financieros → export PDF/HTML.
Diseñado para operar 100% en memoria (sin DB requerida) con `--demo` para entornos sin red.

---

## Rama de desarrollo activa

`claude/real-estate-agent-PUYdh` → repo `basjavier/Flow-1`

---

## Mapa de archivos

### CLI principal
| Archivo | Descripción |
|---|---|
| `run.py` | Entry point completo — argparse, scoring, todos los outputs |

### Scoring
| Archivo | Descripción |
|---|---|
| `scoring/engine.py` | Motor de scoring compuesto + 3 nuevas señales (urgency, flip, loteo) |
| `scoring/corridor_stats.py` | Estadísticas de corredor (precio/m² por tipo+comuna+m²-bucket) |
| `scoring/sii_lookup.py` | Consulta avalúo fiscal SII |

### Scraper
| Archivo | Descripción |
|---|---|
| `scraper/portal_inmobiliario.py` | Playwright + httpx/BS4 · scraping RM completo + por comuna |
| `scraper/base.py` | Utilidades base: normalize_commune, parse_m2, parse_price_clp, rate_limit |
| `scraper/toctoc.py` | Scraper TocToc (complementario) |
| `scraper/yapo.py` | Scraper Yapo (complementario) |

### Reportes
| Archivo | Descripción |
|---|---|
| `reports/pdf_generator.py` | ReportLab — daily digest y commune report |

### API
| Archivo | Descripción |
|---|---|
| `api/main.py` | FastAPI app |
| `api/routes/properties.py` | Endpoint propiedades |
| `api/routes/alerts.py` | Endpoint alertas |
| `api/routes/reports.py` | Endpoint reportes |
| `api/schemas.py` | Pydantic schemas |

### Base de datos
| Archivo | Descripción |
|---|---|
| `database/models.py` | SQLAlchemy models |
| `database/queries.py` | Queries async |
| `database/session.py` | Session factory |
| `database/migrations/env.py` | Alembic config |
| `database/migrations/versions/001_initial_schema.py` | Schema inicial |

### Remates (módulo separado)
| Archivo | Descripción |
|---|---|
| `remates/engine.py` | Motor de análisis de remates |
| `remates/models.py` | Modelos de remates |
| `remates/optimizer.py` | Optimizador de portfolio remates |
| `remates/reports.py` | Reportes de remates |
| `remates/charts.py` | Gráficos de remates |
| `remates/sensitivity.py` | Análisis de sensibilidad remates |

### Config y datos
| Archivo | Descripción |
|---|---|
| `config.py` | Settings pydantic, CORREDORES, PRIORITY_COMMUNES, TERRENO_PRIORITY_COMMUNES |
| `data/tier_a.py` | Datos tier A |
| `scheduler/jobs.py` | Jobs de scheduler |
| `main.py` | Main app entry point |

### Tests
| Archivo | Descripción |
|---|---|
| `tests/test_scoring_engine.py` | 54 tests — engine completo + urgency/flip/loteo |
| `tests/test_scraper_base.py` | Tests de scraper base |

### Outputs generados (no commiteados en producción)
| Archivo | Descripción |
|---|---|
| `rei_fund_memo_*.html` | Investment memo HTML autónomo (--html) |
| `market_intel_*.pdf` | Market Intelligence Report PDF (--corredor) |

---

## Comandos CLI disponibles

```bash
# Datos en vivo (requiere IP residencial — Portal bloquea data centers)
python run.py --top20
python run.py --top20 --tipos terreno
python run.py --top20 --zona "Lampa,Quilicura"

# Modo demo (sin internet, datos de muestra)
python run.py --top20 --demo
python run.py --report --demo
python run.py --invest --demo
python run.py --html --demo
python run.py --dashboard --demo        # ← nuevo: dashboard interactivo HTML
python run.py --corredor --demo
python run.py --corredor --zona "Lampa,Quilicura" --demo
python run.py --subscription-preview --demo

# Opciones globales
--tipos departamento casa terreno   # filtrar por tipo
--pages N                           # páginas por tipo (default 5)
--json                              # output JSON crudo
--demo                              # datos de muestra sin red
```

---

## Arquitectura de scoring

### Score compuesto (0-100)
Sin datos SII: `precio_m2 (55%) + tiempo_mercado (30%) + reduccion_precio (15%)`
Con datos SII: `precio_m2 (40%) + delta_fiscal (30%) + tiempo_mercado (20%) + reduccion_precio (10%)`

### Señales independientes (Module 2)
- **urgency_score**: días > 60 (+40) · reducción > 10% (+35) · precio < 85% avalúo (+25)
- **flip_score**: upside vs mediana (60%) · liquidez comunal (40%)
- **potencial_loteo_score**: precio/ha vs mediana comunal · bonus zonificación ZH +10 · penalidad Ag −10

### Badges en --top20
- `[URGENTE]` si urgency_score ≥ 60
- `[FLIP]` si flip_score ≥ 65
- `[LOTEO]` si potencial_loteo_score ≥ 65

---

## Módulos completados

### [COMPLETADO] CLI base — --top20
- Scraping Portal RM completo (Playwright + httpx fallback)
- Deduplicación por external_id
- Medianas por corredor (tipo + comuna + bucket m²)
- Tabla Rich con score, vs-mediana, días, link
- `--demo` con 77 listings sintéticos realistas

### [COMPLETADO] --report
- Reporte financiero completo con 8 secciones
- KPIs resumen, por tipo, por comuna, distribución scores
- Vendedores motivados, inventario estancado, por corredor, top 5

### [COMPLETADO] --invest
- Memorandum de inversión CFO-grade, 12 secciones
- IRR Newton-Raphson, escenarios Bear/Base/Bull, sensibilidad
- Estructura de fondo (waterfall, hurdle 8% UF, carry 20%)
- Matriz de riesgos, DD checklist, recomendación comité

### [COMPLETADO] --html
- Export self-contained HTML dark-theme (~57KB, sin CDN)
- 9 secciones: cover, KPIs, exec summary, escenarios, pipeline top-10
- Deal cards top-5, heatmap sensibilidad, allocation bars, fund terms, risk matrix + DD

### [COMPLETADO] Módulo 1 — Terrenos para loteo
- `potencial_loteo_score()` en `scoring/engine.py`
- 6 demos periurbanos (Quilicura, Colina, Buin, Paine, Lampa, Batuco)
- Campo `zonificacion` en listings de terreno
- Comunas prioritarias en `config.py`: `TERRENO_PRIORITY_COMMUNES`
- URL scraping: `/venta/terreno/region-metropolitana-de-santiago`

### [COMPLETADO] Módulo 2 — Urgency + Flip filter
- `urgency_score()` y `flip_score()` en `scoring/engine.py`
- Computados en `_score_listing()` por cada propiedad
- Columna **Flags** con badges coloreados en tabla `--top20`
- Tabla de liquidez comunal hardcoded (Las Condes 85 → Batuco 38)

### [COMPLETADO] Refinamientos sesión 2

- **UF actualizado**: 38,500 → 40,100 CLP (Mayo 2026) en todos los puntos de run.py
- **`--dashboard` flag**: HTML interactivo self-contained (~44KB) con filtros JS en tiempo real
  - Tipo (Depto/Casa/Terreno), corredor (Premium/Consolidado/Periurbano), score mínimo slider
  - Badge toggles URGENTE/FLIP/LOTEO, sort por score/precio/días, reset con un click
  - Cards coloreadas por tier de score, KPI header global, contador dinámico
- **Liquidez comunal dinámica**: `_compute_commune_liquidity()` calcula días-en-mercado (50%) + tier de precio vs mediana RM (50%) por cada comuna desde los datos reales; fallback estático si < 3 observaciones

### [COMPLETADO] Módulo 3 — Export para corredores
- `python run.py --corredor [--zona "Lampa,Quilicura"]`
  - Tabla Rich top-15 con columnas Urgente/Flip
  - PDF branded ReportLab dark-theme (fallback .txt si sin ReportLab)
- `python run.py --subscription-preview`
  - Weekly digest top-5 con señales
  - Panel CTA con planes Pro (CLP 49.900/mes) y Fondo (CLP 199.900/mes)

---

## Tests

```bash
python -m pytest tests/ -q
# 54 passed
```

Clases de test:
- `TestScorePriceM2` — 7 tests
- `TestScoreDeltaFiscal` — 6 tests
- `TestScoreTimeOnMarket` — 7 tests
- `TestScorePriceReduction` — 6 tests
- `TestComputeScore` — 12 tests
- `TestClassifyAlertLevel` — 2 tests
- `TestUrgencyScore` — 5 tests
- `TestFlipScore` — 4 tests
- `TestPotencialLoteoScore` — 5 tests

---

## Constantes clave

```python
_UF        = 40_100        # CLP por UF (actualizado Mayo 2026)
FUND_CLP   = 5_000_000_000 # Fondo objetivo CLP 5,000M
HOLD_YEARS = 5             # Horizonte de inversión
HURDLE     = 0.08          # Hurdle rate anual en UF
CARRY      = 0.20          # Carried interest GP
MGMT_FEE   = 0.015         # Management fee anual sobre NAV
```

---

## Notas importantes

- **Portal Inmobiliario bloquea data centers** (Cloudflare 403). Ejecutar desde IP residencial o usar `--demo`.
- `--demo` genera 77 listings sintéticos representativos del mercado RM Mayo 2026.
- Todos los cálculos financieros son en memoria — sin DB requerida.
- IRR calculado con Newton-Raphson (`_irr_newton`) sobre flujos anuales reales.
- El HTML export usa `{{}}` para escapar llaves CSS dentro de f-strings Python.

---

## Próxima sesión

Especificaciones de los módulos implementados en esta sesión (referencia para refinamiento):

### Módulo 1 — Terrenos para loteo (refinamiento)
- Agregar métricas pendientes al scraper: `precio_hectarea`, `hectareas`, `potencial_lotes`, `acceso_agua`, `acceso_luz`
- Scraper específico dedicado para terrenos RM (actualmente usa el scraper general)
- Mejorar extracción de `zonificacion` desde el HTML de Portal (actualmente es inferida)
- Score `potencial_loteo_score` usando mediana dinámica por comuna (actualmente usa corridor_median * 10000)

### Módulo 2 — Urgency + Flip filter (refinamiento)
- ~~Tabla de liquidez comunal dinámica (actualmente hardcoded en `_score_listing`)~~ ✓ **COMPLETADO** (`_compute_commune_liquidity`)
- Integrar `precio_vs_avaluo_ratio` real desde SII lookup cuando disponible
- Backtesting de señales urgency/flip vs velocidad de venta histórica

### Módulo 3 — Export para corredores (refinamiento)
- ~~Dashboard web (HTML interactivo) con filtros por zona y tipo~~ ✓ **COMPLETADO** (`--dashboard`)
- Alertas automáticas Telegram/Email cuando score ≥ 85
- Scheduler semanal para generar y enviar digest automáticamente
- Histórico de precios por corredor (requiere DB)
