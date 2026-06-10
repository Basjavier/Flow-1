import httpx
from datetime import datetime
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential
from config.settings import BCCH_USER, BCCH_PASS, BCCH_SERIES, DEMO_MODE


class BCChScraper:

    BASE_URL = "https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx"

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=20.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def get_serie(
        self,
        serie_key: str,
        first_date: str = "2020-01-01",
        last_date: str = None,
    ) -> list[dict]:

        if not BCCH_USER or not BCCH_PASS:
            logger.warning("BCCh: sin credenciales. Registra en si3.bcentral.cl/siete/")
            return []

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

    def _demo_snapshot(self) -> dict:
        """Snapshot macro sintetico para modo demo (sin credenciales/internet)."""
        today = datetime.now().strftime("%Y-%m-%d")
        demo = {
            "tpm": 5.0, "uf": 38124.5, "dolar": 968.4,
            "inflacion_mensual": 0.4, "imacec": 2.1,
            "credito_bancario": 1.2, "balanza_comercial": 1.2,
        }
        return {k: {"fecha": today, "valor": v, "serie": k} for k, v in demo.items()}

    async def get_macro_snapshot(self) -> dict:
        """Descarga todas las series en paralelo. Retorna último valor de cada una."""
        if DEMO_MODE:
            return self._demo_snapshot()
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
