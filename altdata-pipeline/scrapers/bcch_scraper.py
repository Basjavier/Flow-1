import random
import httpx
from datetime import datetime, timedelta
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential
from config.settings import BCCH_USER, BCCH_PASS, BCCH_SERIES, bcch_live


# Valores base plausibles para Chile (~2026) usados en modo demo.
DEMO_BASE = {
    "tpm":                5.0,
    "uf":             39200.0,
    "dolar":            945.0,
    "inflacion_mensual":  0.4,
    "imacec":           152.0,
    "credito_bancario": 1200.0,
    "balanza_comercial": 1400.0,
}


class BCChScraper:

    BASE_URL = "https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx"

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=20.0)

    def _demo_serie(
        self, serie_key: str, last_date: str | None
    ) -> list[dict]:
        """Serie mensual sintética de 24 puntos terminando hoy."""
        base = DEMO_BASE.get(serie_key, 100.0)
        end = (
            datetime.strptime(last_date, "%Y-%m-%d")
            if last_date else datetime.now()
        ).replace(day=1)
        out = []
        for i in range(23, -1, -1):
            d = end - timedelta(days=30 * i)
            drift = (23 - i) * base * 0.001
            val = round(base * (1 + random.uniform(-0.025, 0.025)) + drift, 4)
            out.append({
                "fecha": d.strftime("%Y-%m-%d"),
                "valor": val,
                "serie": serie_key,
            })
        return out

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def get_serie(
        self,
        serie_key: str,
        first_date: str = "2020-01-01",
        last_date: str = None,
    ) -> list[dict]:

        if not bcch_live():
            logger.info(f"BCCh demo: {serie_key}")
            return self._demo_serie(serie_key, last_date)

        if last_date is None:
            last_date = datetime.now().strftime("%Y-%m-%d")

        serie_id = BCCH_SERIES.get(serie_key, serie_key)

        params = {
            "user":       BCCH_USER,
            "pass":       BCCH_PASS,
            "firstdate":  first_date,
            "lastdate":   last_date,
            "timeseries": serie_id,
            "function":   "GetSeries",
            "format":     "json",
        }

        response = await self.client.get(self.BASE_URL, params=params)

        if response.status_code != 200:
            raise Exception(f"BCCh HTTP {response.status_code}")

        data = response.json()

        if "Series" not in data:
            logger.warning(f"BCCh: sin datos para {serie_key}")
            return []

        obs_list = data["Series"].get("Obs", [])
        return [
            {
                "fecha": obs["indexDateString"],
                "valor": float(obs["value"]) if obs.get("value") not in (None, "N/E", "") else None,
                "serie": serie_key,
            }
            for obs in obs_list
            if obs.get("value") not in (None, "N/E", "")
        ]

    async def get_macro_snapshot(self) -> dict:
        """Descarga todas las series en paralelo. Retorna último valor de cada una."""
        import asyncio

        async def fetch_one(key):
            try:
                data = await self.get_serie(key, first_date="2023-01-01")
                if data:
                    return key, data[-1]
            except Exception as e:
                logger.warning(f"BCCh {key} error: {e}")
            return key, None

        tasks = [fetch_one(k) for k in BCCH_SERIES.keys()]
        results = await asyncio.gather(*tasks)

        return {key: val for key, val in results if val is not None}
