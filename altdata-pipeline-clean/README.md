# SysFund AltData

Pipeline de datos alternativos para un fondo systematic chileno: scrapea CMF
(hechos esenciales), BCCh (macro) y Portal Inmobiliario; corre los hechos por
Claude para extraer señales accionables; guarda todo en DuckDB; expone una API
FastAPI y un dashboard React.

## Arranque rápido

```bash
bash scripts/setup.sh                    # crea .venv e instala deps
cp .env.template .env                    # completá ANTHROPIC_API_KEY + BCCH_USER/PASS
source .venv/bin/activate

python main.py --demo --once             # puebla todo con datos sintéticos
python main.py --api-only                # sirve API + dashboard en :8003
```

Abrí **http://localhost:8003/dashboard/** y listo.

## Modos de ejecución

| Comando | Qué hace |
|---|---|
| `python main.py --test` | Chequea conectividad: DuckDB, Anthropic, BCCh, Bloomberg. |
| `python main.py --demo --once` | Pipeline 1 vez con datos sintéticos (sin internet ni key). |
| `python main.py --once` | Pipeline 1 vez contra fuentes reales. |
| `python main.py --api-only` | Solo API + dashboard (no corre scraping). |
| `python main.py` | Modo completo: API + scheduler (CMF cada 5min, BCCh diario, RE semanal). |

`--demo` se puede combinar con cualquier modo (`--demo`, `--demo --once`).

## Endpoints

Todos los endpoints están bajo `http://localhost:8003`:

| Endpoint | Devuelve |
|---|---|
| `GET /` | Health check. |
| `GET /signals?min_confidence=0.7` | Señales activas (CMF + NLP). |
| `GET /signals/by-ticker/{ticker}` | Señales filtradas por ticker. |
| `GET /macro` | Snapshot macro BCCh (último valor por serie). |
| `GET /prices` | Precios actuales de los tickers (Bloomberg, demo si no hay terminal). |
| `GET /prices/{ticker}/history?days=120` | Histórico para el gráfico. |
| `GET /real-estate?comuna=Las Condes` | Top deals por UF/m² vs mediana de la comuna. |
| `GET /real-estate/{comuna}` | Stats RE de una comuna. |
| `GET /pipeline/status` | Últimas corridas del orquestador. |
| `GET /cost` | Tokens y costo estimado del NLP. |
| `GET /dashboard/` | Dashboard React. |

## Dashboard

El dashboard puede correrse de dos maneras:

1. **Servido por la API** (recomendado): `http://localhost:8003/dashboard/` —
   mismo origen, sin CORS.
2. **Archivo suelto**: abrir `dashboard/index.html` directo en el browser —
   pega a `http://localhost:8003` por defecto. Override con
   `dashboard/index.html?api=http://otro-host:puerto`.

El dot verde del header indica que la API responde; ámbar es modo offline
(cae a datos de muestra para no quedar en blanco).

## Troubleshooting

- **`anthropic FAIL 401`** — la key del `.env` está vencida o vacía. Si no
  ponés key, el NLP cae a heurística (sirve para demo, no para producción).
- **`bcch FAIL`** — credenciales mal o sin red. Registro gratis en
  si3.bcentral.cl. En `--demo` se ignora.
- **CMF / Portal Inmobiliario devuelven 0 filas** — los selectores HTML
  pueden necesitar ajuste cuando los sitios cambian de estructura. Mirá
  `scrapers/cmf_scraper.py::_scrape_portal` y `scrapers/portal_inmobiliario.py`.
- **`Bloomberg: blpapi no instalado`** — esperado fuera de una Terminal. El
  connector devuelve datos demo (random walk) para no romper el dashboard.

## Seguridad

El `.env` está en `.gitignore`. Si tu `ANTHROPIC_API_KEY` viajó por un canal
inseguro (ej: zip de demo), **rotala** en console.anthropic.com antes de
poner el pipeline en producción.
