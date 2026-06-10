import json
import re
from datetime import datetime
from anthropic import Anthropic
from tenacity import retry, stop_after_attempt, wait_exponential
from loguru import logger
from config.settings import NLP_MODEL_HIGH_VOLUME, NLP_MODEL_MACRO, ANTHROPIC_API_KEY, DEMO_MODE


def _parse_llm_json(raw: str) -> dict | None:
    """
    Limpia code fences ```json ... ``` y texto antes/despues que a veces
    devuelven los LLMs, y parsea a dict. Retorna None si no se pudo.
    """
    if not raw:
        return None
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```(?:json)?\s*", "", txt)
        txt = re.sub(r"\s*```\s*$", "", txt)
    if not txt.startswith("{"):
        s, e = txt.find("{"), txt.rfind("}")
        if s != -1 and e != -1 and e > s:
            txt = txt[s : e + 1]
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        return None


class NLPProcessor:

    def __init__(self):
        # Sin key (o en demo) no instanciamos cliente: las señales se generan
        # localmente con heurística para que el pipeline corra offline.
        self.client = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

    def _demo_signal(self, hecho: dict) -> dict:
        """Señal sintética derivada del hecho (modo demo / sin API key)."""
        tipo    = (hecho.get("tipo_documento") or "").lower()
        desc    = (hecho.get("descripcion") or "").lower()
        empresa = hecho.get("empresa") or "N/A"
        if "adquis" in tipo or "opa" in desc or "fusi" in tipo:
            sig, conf, urg, cat = "BULLISH", 0.91, "HIGH", "MA"
            action = f"LONG {empresa} CI — target M&A"
        elif "dividendo" in tipo or "dividendo" in desc:
            sig, conf, urg, cat = "BULLISH", 0.86, "HIGH", "DIVIDEND"
            action = f"LONG {empresa} CI — yield play"
        elif "deuda" in tipo or "bono" in desc:
            sig, conf, urg, cat = "BEARISH", 0.74, "MEDIUM", "DEBT_ISSUANCE"
            action = f"REDUCE {empresa} CI — dilución potencial"
        elif "directivo" in tipo or "cfo" in desc or "ceo" in desc:
            sig, conf, urg, cat = "WATCHLIST", 0.66, "LOW", "MANAGEMENT_CHANGE"
            action = "Monitor — esperar confirmación"
        elif "contrato" in tipo or "offtake" in desc or "acuerdo" in desc:
            sig, conf, urg, cat = "BULLISH", 0.82, "MEDIUM", "OTHER"
            action = f"ADD {empresa} CI — re-rating por contrato"
        else:
            sig, conf, urg, cat = "NEUTRAL", 0.60, "LOW", "OTHER"
            action = None
        return {
            "signal":          sig,
            "confidence":      conf,
            "category":        cat,
            "time_horizon":    "DAYS",
            "affected_ticker": empresa,
            "affected_sector": None,
            "reasoning":       (hecho.get("descripcion") or "")[:160],
            "action":          action,
            "urgency":         urg,
            "source_type":     "CMF_HE",
            "source_date":     hecho.get("fecha"),
            "processed_at":    datetime.now().isoformat(),
            "hecho_id":        hecho.get("id"),
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def extract_signal_cmf(self, hecho: dict) -> dict | None:
        if DEMO_MODE or not ANTHROPIC_API_KEY:
            return self._demo_signal(hecho)
        prompt = f"""Eres un analista senior de hedge fund systematic.
Analiza este hecho esencial CMF y extrae senal de trading.

Empresa: {hecho.get('empresa')}
Fecha: {hecho.get('fecha')}
Tipo: {hecho.get('tipo_documento')}
Descripcion: {hecho.get('descripcion')}

Responde SOLO con JSON valido (sin backticks, sin texto extra):
{{
  "signal": "BULLISH"|"BEARISH"|"NEUTRAL"|"WATCHLIST",
  "confidence": 0.0-1.0,
  "category": "MA"|"DEBT_ISSUANCE"|"MANAGEMENT_CHANGE"|"EARNINGS_GUIDANCE"|"REGULATORY"|"DIVIDEND"|"OTHER",
  "time_horizon": "INTRADAY"|"DAYS"|"WEEKS"|"MONTHS",
  "affected_ticker": "ticker BCS sin sufijo o null",
  "affected_sector": "sector o null",
  "reasoning": "max 2 oraciones nivel desk",
  "action": "accion concreta o null",
  "urgency": "HIGH"|"MEDIUM"|"LOW"
}}

Reglas:
- M&A como target -> BULLISH HIGH
- Emision deuda masiva -> revisar ratio, BEARISH MEDIUM
- Cambio controlador -> WATCHLIST siempre
- Dividendo extraordinario -> BULLISH si yield > 4%
- Sin impacto -> NEUTRAL LOW"""

        try:
            response = self.client.messages.create(
                model=NLP_MODEL_HIGH_VOLUME,
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text
            data = _parse_llm_json(raw)
            if data is None:
                logger.warning(f"JSON parse error CMF NLP | raw: {raw[:200]}")
                return None
            data["source_type"]   = "CMF_HE"
            data["source_date"]   = hecho.get("fecha")
            data["processed_at"]  = datetime.now().isoformat()
            data["hecho_id"]      = hecho.get("id")
            return data
        except Exception as e:
            logger.error(f"NLP CMF error: {e}")
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def analyze_bcch_release(self, text: str) -> dict | None:
        prompt = f"""Eres macro strategist de hedge fund.
Analiza este comunicado del Banco Central de Chile.

{text[:3000]}

Responde SOLO con JSON valido:
{{
  "tpm_direction": "HIKE"|"CUT"|"HOLD"|"UNCLEAR",
  "tpm_bps_expected": numero o null,
  "inflation_bias": "HAWKISH"|"DOVISH"|"NEUTRAL",
  "growth_assessment": "POSITIVE"|"NEGATIVE"|"NEUTRAL",
  "clp_impact": "STRENGTHENING"|"WEAKENING"|"NEUTRAL",
  "rate_curve_impact": "STEEPENING"|"FLATTENING"|"NEUTRAL",
  "key_phrase": "frase mas importante",
  "confidence": 0.0-1.0,
  "trades": [
    {{"instrument": "nombre", "direction": "LONG"|"SHORT", "reasoning": "una oracion"}}
  ]
}}"""

        try:
            response = self.client.messages.create(
                model=NLP_MODEL_MACRO,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text
            data = _parse_llm_json(raw)
            if data is None:
                logger.warning(f"JSON parse error BCCh NLP | raw: {raw[:200]}")
            return data
        except Exception as e:
            logger.error(f"NLP BCCh error: {e}")
            return None

    async def score_property(self, prop: dict, stats: dict) -> dict:
        """Scoring simple de propiedad vs mercado. Pura logica local."""
        pct_vs_market = None
        if stats.get("median_uf_m2") and prop.get("precio_uf_m2"):
            pct_vs_market = round(
                (prop["precio_uf_m2"] / stats["median_uf_m2"] - 1) * 100, 1
            )

        score = 5.0
        if pct_vs_market is not None:
            if pct_vs_market < -15:  score = 9.0
            elif pct_vs_market < -8: score = 7.5
            elif pct_vs_market < -3: score = 6.5
            elif pct_vs_market > 15: score = 2.0
            elif pct_vs_market > 8:  score = 3.5

        return {
            "score":          round(score, 1),
            "pct_vs_market":  pct_vs_market,
            "deal_flag":      score >= 7.5,
            "median_uf_m2":   stats.get("median_uf_m2"),
        }
