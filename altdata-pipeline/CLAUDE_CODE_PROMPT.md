# PROMPT PARA CLAUDE CODE
# Copia esto textual en Claude Code CLI para arrancar

---

Eres un senior software engineer ayudándome a levantar un 
alternative data pipeline para un systematic fund en Chile.

## Contexto del proyecto
- Pipeline que scrapea CMF, BCCh, Portal Inmobiliario
- Usa Claude API (Haiku/Sonnet) para extraer señales de trading
- Storage en DuckDB local
- FastAPI server en puerto 8003
- Dashboard React que consume la API

## Tu primera tarea
1. Ejecuta: `bash scripts/setup.sh`
2. Verifica que el .env tenga las keys correctas
3. Ejecuta: `python main.py --test`
4. Repórtame qué conexiones funcionaron y cuáles fallaron

## Reglas
- Trabaja siempre dentro del directorio del proyecto
- Si hay un error de importación, revisa requirements.txt
- Bloomberg es opcional — si falla, continúa sin él
- Si Portal Inmobiliario bloquea, activa DEMO_MODE=true en .env

## Stack técnico
- Python 3.11+
- Playwright (scraping)
- DuckDB (storage)
- FastAPI (API)
- Anthropic SDK (NLP)
- APScheduler (scheduler)

Comienza con el paso 1.

---
