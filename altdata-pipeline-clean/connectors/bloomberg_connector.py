"""
Bloomberg Terminal Connector via BLPAPI.

REQUISITO: Bloomberg Terminal abierto y logueado.
INSTALACIÓN: pip install blpapi

Si no tienes blpapi instalado, el conector retorna datos demo.
"""

import pandas as pd
from datetime import datetime, timedelta
from loguru import logger
from config.settings import BBG_HOST, BBG_PORT, TICKERS_BBG


class BloombergConnector:

    def __init__(self):
        self._available = False
        self._session = None
        self._try_connect()

    def _try_connect(self):
        try:
            import blpapi
            options = blpapi.SessionOptions()
            options.setServerHost(BBG_HOST)
            options.setServerPort(BBG_PORT)

            session = blpapi.Session(options)
            if session.start() and session.openService("//blp/refdata"):
                self._session = session
                self._available = True
                logger.success("✅ Bloomberg Terminal conectado")
            else:
                logger.warning("Bloomberg: Terminal no disponible")
        except ImportError:
            logger.warning("Bloomberg: blpapi no instalado (pip install blpapi)")
        except Exception as e:
            logger.warning(f"Bloomberg: {e}")

    @property
    def available(self) -> bool:
        return self._available

    def get_historical(
        self,
        ticker_key: str,
        fields: list[str] = ["PX_LAST", "VOLUME"],
        start: str = None,
        end: str = None,
    ) -> pd.DataFrame:

        if not self._available:
            return self._demo_historical(ticker_key, fields)

        import blpapi
        ticker = TICKERS_BBG.get(ticker_key, ticker_key)
        start = (start or (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")).replace("-", "")
        end   = (end or datetime.now().strftime("%Y-%m-%d")).replace("-", "")

        refSvc = self._session.getService("//blp/refdata")
        req = refSvc.createRequest("HistoricalDataRequest")
        req.getElement("securities").appendValue(ticker)
        for f in fields:
            req.getElement("fields").appendValue(f)
        req.set("startDate", start)
        req.set("endDate", end)
        req.set("periodicitySelection", "DAILY")

        self._session.sendRequest(req)
        records = []

        while True:
            event = self._session.nextEvent(500)
            for msg in event:
                if msg.hasElement("securityData"):
                    fd = msg.getElement("securityData").getElement("fieldData")
                    for i in range(fd.numValues()):
                        row = fd.getValueAsElement(i)
                        rec = {"date": row.getElementAsDatetime("date")}
                        for f in fields:
                            try: rec[f] = row.getElementAsFloat(f)
                            except: rec[f] = None
                        records.append(rec)
            if event.eventType() == 5:  # RESPONSE
                break

        df = pd.DataFrame(records)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
        return df

    def get_current_price(self, ticker_keys: list[str]) -> dict:
        if not self._available:
            return self._demo_prices(ticker_keys)

        import blpapi
        tickers = [TICKERS_BBG.get(k, k) for k in ticker_keys]
        refSvc = self._session.getService("//blp/refdata")
        req = refSvc.createRequest("ReferenceDataRequest")
        for t in tickers: req.getElement("securities").appendValue(t)
        for f in ["PX_LAST", "CHG_PCT_1D", "VOLUME"]:
            req.getElement("fields").appendValue(f)

        self._session.sendRequest(req)
        prices = {}

        while True:
            event = self._session.nextEvent(500)
            for msg in event:
                if msg.hasElement("securityData"):
                    arr = msg.getElement("securityData")
                    for i in range(arr.numValues()):
                        sec = arr.getValueAsElement(i)
                        ticker = sec.getElementAsString("security")
                        fd = sec.getElement("fieldData")
                        key = next((k for k,v in TICKERS_BBG.items() if v==ticker), ticker)
                        prices[key] = {
                            "price":   self._sf(fd, "PX_LAST"),
                            "chg_pct": self._sf(fd, "CHG_PCT_1D"),
                            "volume":  self._sf(fd, "VOLUME"),
                        }
            if event.eventType() == 5:
                break

        return prices

    def _sf(self, el, field):
        try: return el.getElementAsFloat(field)
        except: return None

    def disconnect(self):
        if self._session:
            self._session.stop()
            logger.info("Bloomberg desconectado")

    # ─── DEMO DATA ────────────────────────────────────
    def _demo_historical(self, ticker_key: str, fields: list[str]) -> pd.DataFrame:
        import numpy as np
        n = 365
        dates = pd.date_range(end=datetime.now(), periods=n, freq="B")
        base = {"ipsa": 7000, "falabella": 900, "sqm": 48000}.get(ticker_key, 1000)
        prices = base * np.cumprod(1 + np.random.normal(0.0003, 0.012, n))
        df = pd.DataFrame({"PX_LAST": prices}, index=dates)
        if "VOLUME" in fields:
            df["VOLUME"] = np.random.randint(1_000_000, 10_000_000, n)
        return df

    def _demo_prices(self, ticker_keys: list[str]) -> dict:
        demos = {
            "ipsa":        {"price": 7234.5, "chg_pct": 0.42,  "volume": 45_000_000},
            "falabella":   {"price": 892.3,  "chg_pct": -1.23, "volume": 12_000_000},
            "sqm":         {"price": 48240,  "chg_pct": 2.17,  "volume": 8_000_000},
            "clp_usd":     {"price": 968.4,  "chg_pct": -0.22, "volume": None},
        }
        return {k: demos.get(k, {"price": 1000, "chg_pct": 0, "volume": None})
                for k in ticker_keys}
