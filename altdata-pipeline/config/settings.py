import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# ─── PATHS ────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent.parent
DATA_DIR    = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DUCKDB_PATH   = os.getenv("DUCKDB_PATH", str(DATA_DIR / "altdata.duckdb"))
CHROMADB_PATH = os.getenv("CHROMADB_PATH", str(DATA_DIR / "chromadb"))

# ─── ANTHROPIC ────────────────────────────────────────
ANTHROPIC_API_KEY     = os.getenv("ANTHROPIC_API_KEY", "")
NLP_MODEL_HIGH_VOLUME = os.getenv("NLP_MODEL_HIGH_VOLUME", "claude-haiku-4-5-20251001")
NLP_MODEL_MACRO       = os.getenv("NLP_MODEL_MACRO", "claude-sonnet-4-6")

# ─── BCCH ─────────────────────────────────────────────
BCCH_USER = os.getenv("BCCH_USER", "")
BCCH_PASS = os.getenv("BCCH_PASS", "")

# ─── BLOOMBERG ────────────────────────────────────────
BBG_HOST = os.getenv("BBG_HOST", "localhost")
BBG_PORT = int(os.getenv("BBG_PORT", 8194))

# ─── API ──────────────────────────────────────────────
API_HOST       = os.getenv("API_HOST", "0.0.0.0")
API_PORT       = int(os.getenv("API_PORT", 8003))
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "dev-key")

# ─── PIPELINE ─────────────────────────────────────────
SCRAPE_START_HOUR = int(os.getenv("SCRAPE_START_HOUR", 9))
SCRAPE_END_HOUR   = int(os.getenv("SCRAPE_END_HOUR", 18))
DEMO_MODE         = os.getenv("DEMO_MODE", "false").lower() == "true"

ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")

# ─── TICKERS CHILE ────────────────────────────────────
TICKERS_BBG = {
    "ipsa":        "IPSA Index",
    "falabella":   "FALABELLA CI Equity",
    "sqm":         "SQM/B CI Equity",
    "cencosud":    "CENCOSUD CI Equity",
    "copec":       "COPEC CI Equity",
    "banco_chile": "CHILE CI Equity",
    "entel":       "ENTEL CI Equity",
    "colbun":      "COLBUN CI Equity",
    "cmpc":        "CMPC CI Equity",
    "latam":       "LTM CI Equity",
    "tpm":         "BCHTPM Index",
    "uf":          "BCUF Index",
    "clp_usd":     "USDCLP Curncy",
    "bono_10y":    "GTCLO10Y Govt",
}

# ─── BCCH SERIES ──────────────────────────────────────
BCCH_SERIES = {
    "tpm":               "F022.BCH.INT.010.M",
    "uf":                "F073.TCO.PRE.Z.D",
    "dolar":             "F073.TCO.PRE.Z.D",
    "inflacion_mensual": "F073.IPC.VAR.Z.M",
    "imacec":            "F032.IMC.IND.10.M",
    "credito_bancario":  "F019.IBC.FLU.M.CLP",
    "balanza_comercial": "F073.BAC.BCO.Z.M",
}
