import json
from datetime import datetime
from anthropic import Anthropic
from tenacity import retry, stop_after_attempt, wait_exponential
from loguru import logger
from config.settings import (
    NLP_MODEL_HIGH_VOLUME, NLP_MODEL_MACRO, ANTHROPIC_API_KEY,
    anthropic_live,
)


# Mapeo simple empresa → ticker BCS para el fallback por reglas.
_TICKERS = {
    "falabella": "FALABELLA", "sqm": "SQM", "cencosud": "CENCOSUD",
    "copec": "COPEC", "enel": "ENELCHILE", "cmpc": "CMPC",
    "banco de chile": "CHILE", "colbun": "COLBUN", "entel": "ENTEL",
    "latam": "LTM",
}


class NLPProcessor:

    def __init__(self):
        self._live = anthropic_live()
        # El cliente solo se usa si hay key real; con placeholder no se crea.
        self.client = Anthropic(api_key=ANTHROPIC_API_KEY) if self._live else None
        if not self._live:
            logger.info("NLP en modo demo: fallback por reglas (sin Anthropic)")

    # ─── FALLBACK POR REGLAS ──────────────────────────
    def _guess_ticker(self, empresa: str | None) -> str | None:
        e = (empresa or "").lower()
        for k, v in _TICKERS.items():
            if k in e:
                return v
        return None

    def _rule_signal_cmf(self, hecho: dict) -> dict:
        text = (
            f"{hecho.get('tipo_documento','')} {hecho.get('descripcion','')}"
        ).lower()

        if any(w in text for w in (
            "adquisic", "fusión", "fusion", "oferta pública", "opa", "m&a",
        )):
            sig, conf, cat, hor, urg = "BULLISH", 0.83, "MA", "WEEKS", "HIGH"
            reason = "Operación M&A con la empresa como target; típicamente re-rating al alza."
            action = "Evaluar long en el target con stop bajo precio pre-anuncio."
        elif any(w in text for w in (
            "bonos", "deuda", "colocación", "colocacion", "refinanciamiento",
        )):
            sig, conf, cat, hor, urg = "BEARISH", 0.62, "DEBT_ISSUANCE", "WEEKS", "MEDIUM"
            reason = "Emisión de deuda relevante; revisar leverage y cobertura de intereses."
            action = "Monitorear spread de crédito y ratio Deuda/EBITDA."
        elif any(w in text for w in (
            "controlador", "pacto de accionistas", "toma de control",
        )):
            sig, conf, cat, hor, urg = "WATCHLIST", 0.70, "MANAGEMENT_CHANGE", "DAYS", "MEDIUM"
            reason = "Cambio de controlador; gobernanza y estrategia en revisión."
            action = "Watchlist hasta conocer términos del nuevo controlador."
        elif "dividendo" in text:
            sig, conf, cat, hor, urg = "BULLISH", 0.60, "DIVIDEND", "DAYS", "LOW"
            reason = "Dividendo extraordinario; soporte de precio si el yield es atractivo."
            action = "Estimar yield vs. comparables antes de tomar posición."
        elif any(w in text for w in (
            "sanción", "sancion", "multa", "regulator", "fiscaliz", "superintendencia",
        )):
            sig, conf, cat, hor, urg = "BEARISH", 0.58, "REGULATORY", "DAYS", "MEDIUM"
            reason = "Acción regulatoria con potencial impacto financiero y reputacional."
            action = "Cuantificar multa vs. utilidad anual."
        elif any(w in text for w in (
            "resultado", "utilidad", "ebitda", "guidance", "estados financieros",
        )):
            sig, conf, cat, hor, urg = "NEUTRAL", 0.50, "EARNINGS_GUIDANCE", "DAYS", "LOW"
            reason = "Resultados en línea con expectativas; sin sorpresa material."
            action = None
        else:
            sig, conf, cat, hor, urg = "NEUTRAL", 0.40, "OTHER", "DAYS", "LOW"
            reason = "Hecho sin impacto direccional claro."
            action = None

        ticker = self._guess_ticker(hecho.get("empresa"))
        return {
            "signal":          sig,
            "confidence":      conf,
            "category":        cat,
            "time_horizon":    hor,
            "affected_ticker": ticker,
            "affected_sector": None,
            "reasoning":       reason,
            "action":          action,
            "urgency":         urg,
            "source_type":     "CMF_HE",
            "source_date":     hecho.get("fecha"),
            "processed_at":    datetime.now().isoformat(),
            "hecho_id":        hecho.get("id"),
            "engine":          "rules",
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def extract_signal_cmf(self, hecho: dict) -> dict | None:
        if not self._live:
            return self._rule_signal_cmf(hecho)

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
        if not self._live:
            return {
                "tpm_direction":     "HOLD",
                "tpm_bps_expected":  0,
                "inflation_bias":    "NEUTRAL",
                "growth_assessment": "NEUTRAL",
                "clp_impact":        "NEUTRAL",
                "rate_curve_impact": "NEUTRAL",
                "key_phrase":        "(demo) sin comunicado real procesado",
                "confidence":        0.4,
                "trades":            [],
                "engine":            "rules",
            }

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
