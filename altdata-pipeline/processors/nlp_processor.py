import json
from datetime import datetime
from anthropic import Anthropic
from tenacity import retry, stop_after_attempt, wait_exponential
from loguru import logger
from config.settings import NLP_MODEL_HIGH_VOLUME, NLP_MODEL_MACRO, ANTHROPIC_API_KEY


class NLPProcessor:

    def __init__(self):
        self.client = Anthropic(api_key=ANTHROPIC_API_KEY)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def extract_signal_cmf(self, hecho: dict) -> dict | None:
        prompt = f"""Eres un analista senior de hedge fund systematic.
Analiza este hecho esencial CMF y extrae señal de trading.

Empresa: {hecho.get('empresa')}
Fecha: {hecho.get('fecha')}
Tipo: {hecho.get('tipo_documento')}
Descripción: {hecho.get('descripcion')}

Responde SOLO con JSON válido (sin backticks, sin texto extra):
{{
  "signal": "BULLISH"|"BEARISH"|"NEUTRAL"|"WATCHLIST",
  "confidence": 0.0-1.0,
  "category": "MA"|"DEBT_ISSUANCE"|"MANAGEMENT_CHANGE"|"EARNINGS_GUIDANCE"|"REGULATORY"|"DIVIDEND"|"OTHER",
  "time_horizon": "INTRADAY"|"DAYS"|"WEEKS"|"MONTHS",
  "affected_ticker": "ticker BCS sin sufijo o null",
  "affected_sector": "sector o null",
  "reasoning": "max 2 oraciones nivel desk",
  "action": "acción concreta o null",
  "urgency": "HIGH"|"MEDIUM"|"LOW"
}}

Reglas:
- M&A como target → BULLISH HIGH
- Emisión deuda masiva → revisar ratio, BEARISH MEDIUM
- Cambio controlador → WATCHLIST siempre
- Dividendo extraordinario → BULLISH si yield > 4%
- Sin impacto → NEUTRAL LOW"""

        try:
            response = self.client.messages.create(
                model=NLP_MODEL_HIGH_VOLUME,
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            data = json.loads(raw)
            data["source_type"]   = "CMF_HE"
            data["source_date"]   = hecho.get("fecha")
            data["processed_at"]  = datetime.now().isoformat()
            data["hecho_id"]      = hecho.get("id")
            return data
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error CMF NLP: {e} | raw: {raw[:200]}")
            return None
        except Exception as e:
            logger.error(f"NLP CMF error: {e}")
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def analyze_bcch_release(self, text: str) -> dict | None:
        prompt = f"""Eres macro strategist de hedge fund.
Analiza este comunicado del Banco Central de Chile.

{text[:3000]}

Responde SOLO con JSON válido:
{{
  "tpm_direction": "HIKE"|"CUT"|"HOLD"|"UNCLEAR",
  "tpm_bps_expected": número o null,
  "inflation_bias": "HAWKISH"|"DOVISH"|"NEUTRAL",
  "growth_assessment": "POSITIVE"|"NEGATIVE"|"NEUTRAL",
  "clp_impact": "STRENGTHENING"|"WEAKENING"|"NEUTRAL",
  "rate_curve_impact": "STEEPENING"|"FLATTENING"|"NEUTRAL",
  "key_phrase": "frase más importante",
  "confidence": 0.0-1.0,
  "trades": [
    {{"instrument": "nombre", "direction": "LONG"|"SHORT", "reasoning": "una oración"}}
  ]
}}"""

        try:
            response = self.client.messages.create(
                model=NLP_MODEL_MACRO,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            return json.loads(raw)
        except Exception as e:
            logger.error(f"NLP BCCh error: {e}")
            return None

    async def score_property(self, prop: dict, stats: dict) -> dict:
        """Scoring simple de propiedad vs mercado. Usa Haiku (muy barato)."""
        pct_vs_market = None
        if stats.get("median_uf_m2") and prop.get("precio_uf_m2"):
            pct_vs_market = round(
                (prop["precio_uf_m2"] / stats["median_uf_m2"] - 1) * 100, 1
            )

        score = 5.0  # neutral
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
