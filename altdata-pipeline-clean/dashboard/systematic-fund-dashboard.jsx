
import { useState, useEffect, useRef } from "react";
import {
  LineChart, Line, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine
} from "recharts";

// ─── SYNTHETIC DATA ENGINE ───────────────────────────────────────────────────

const TICKERS = [
  { key: "IPSA", name: "IPSA Index", price: 7234.5, chg: 0.42 },
  { key: "FALABELLA", name: "Falabella CI", price: 892.3, chg: -1.23 },
  { key: "SQM/B", name: "SQM-B CI", price: 48240, chg: 2.17 },
  { key: "CENCOSUD", name: "Cencosud CI", price: 1456.7, chg: 0.88 },
  { key: "COPEC", name: "Copec CI", price: 7890.0, chg: -0.34 },
  { key: "CHILE", name: "Banco Chile CI", price: 98.45, chg: 0.15 },
  { key: "USDCLP", name: "USD/CLP", price: 968.4, chg: -0.22 },
  { key: "BCH TPM", name: "TPM BCCh", price: 5.0, chg: 0.0 },
];

const CMF_SIGNALS = [
  {
    id: "S001", time: "09:47", empresa: "FALABELLA",
    tipo: "Adquisición", signal: "BULLISH", conf: 0.91, urgency: "HIGH",
    action: "LONG FALABELLA CI — target +18%",
    reasoning: "OPA sobre filial retail. Premio histórico promedio 23% sobre precio previo.",
    cat: "MA",
  },
  {
    id: "S002", time: "10:12", empresa: "SQM",
    tipo: "Hecho Esencial", signal: "BULLISH", conf: 0.83, urgency: "MEDIUM",
    action: "ADD SQM/B CI — litio/potasio rerating",
    reasoning: "Acuerdo offtake con fabricante asiático de baterías. Volumen +40% YoY.",
    cat: "CONTRACT",
  },
  {
    id: "S003", time: "11:30", empresa: "ENTEL",
    tipo: "Cambio Directivo", signal: "WATCHLIST", conf: 0.67, urgency: "LOW",
    action: "Monitor — esperar confirmación nuevo CEO",
    reasoning: "Salida CFO no planificada. Precedente negativo en sector telecom CL.",
    cat: "MGMT",
  },
  {
    id: "S004", time: "13:05", empresa: "LATAM",
    tipo: "Emisión Deuda", signal: "BEARISH", conf: 0.76, urgency: "MEDIUM",
    action: "REDUCE LTM CI — dilución potencial",
    reasoning: "Bono UF 5M a 10 años. Ratio deuda/equity supera umbral histórico.",
    cat: "DEBT",
  },
  {
    id: "S005", time: "14:22", empresa: "COPEC",
    tipo: "Dividendo Extraordinario", signal: "BULLISH", conf: 0.88, urgency: "HIGH",
    action: "LONG COPEC CI — yield play",
    reasoning: "Dividendo especial CLP 450/acción. Yield implícito 5.7% sobre precio actual.",
    cat: "DIVIDEND",
  },
];

const MACRO_DATA = [
  { label: "TPM BCCh", value: "5.00%", delta: "0 bps", trend: "neutral" },
  { label: "UF Hoy", value: "38,124.5", delta: "+0.03%", trend: "up" },
  { label: "USD/CLP", value: "968.4", delta: "-2.1", trend: "down" },
  { label: "IPC Mensual", value: "+0.4%", delta: "vs +0.3% esp.", trend: "up" },
  { label: "IMACEC YoY", value: "+2.1%", delta: "↑ desde 1.8%", trend: "up" },
  { label: "Bal. Comercial", value: "USD +1.2B", delta: "+340M MoM", trend: "up" },
];

const generatePriceSeries = (base, n = 120, vol = 0.012) => {
  const data = [];
  let price = base;
  const now = new Date();
  for (let i = n; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    price *= 1 + (Math.random() - 0.48) * vol;
    data.push({
      date: d.toLocaleDateString("es-CL", { month: "short", day: "numeric" }),
      price: parseFloat(price.toFixed(2)),
    });
  }
  return data;
};

const generateCAR = () => {
  const data = [];
  let car = 0;
  for (let i = -5; i <= 10; i++) {
    car += (Math.random() - 0.35) * 0.8;
    data.push({ day: i, car: parseFloat(car.toFixed(2)) });
  }
  return data;
};

const generatePortfolioHistory = () => {
  const data = [];
  let val = 1000000;
  let bench = 1000000;
  const now = new Date();
  for (let i = 180; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    val *= 1 + (Math.random() - 0.46) * 0.014;
    bench *= 1 + (Math.random() - 0.48) * 0.011;
    data.push({
      date: d.toLocaleDateString("es-CL", { month: "short", day: "numeric" }),
      portfolio: Math.round(val),
      benchmark: Math.round(bench),
    });
  }
  return data;
};

// ─── LIVE API LAYER ───────────────────────────────────────────────────────────
// Conecta el dashboard al backend FastAPI. Si la API no responde (o un panel
// viene vacio) se cae a los datos de muestra de arriba, asi la vista nunca queda
// en blanco. Apunta a otro host con  ?api=http://host:puerto
const API_BASE =
  new URLSearchParams(window.location.search).get("api") ||
  "http://localhost:8003";

async function apiGet(path) {
  const r = await fetch(API_BASE + path, { mode: "cors" });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

const MACRO_LABELS = {
  tpm: "TPM BCCh", uf: "UF Hoy", dolar: "USD/CLP",
  inflacion_mensual: "IPC Mensual", imacec: "IMACEC YoY",
  balanza_comercial: "Bal. Comercial", credito_bancario: "Crédito Bancario",
};

function mapSignals(api) {
  if (!api || !api.signals || !api.signals.length) return null;
  return api.signals.map((s, i) => ({
    id:       s.id || ("S" + i),
    time:     (s.processed_at || "").slice(11, 16) || "--:--",
    empresa:  s.affected_ticker || s.affected_sector || "—",
    tipo:     s.category || s.source_type || "HECHO",
    signal:   s.signal || "NEUTRAL",
    conf:     typeof s.confidence === "number" ? s.confidence : 0,
    urgency:  s.urgency || "LOW",
    action:   s.action || "—",
    reasoning: s.reasoning || "",
    cat:      s.category || "OTHER",
  }));
}

function mapMacro(api) {
  const keys = api ? Object.keys(api) : [];
  if (!keys.length) return null;
  return keys.map(k => ({
    label: MACRO_LABELS[k] || k.toUpperCase(),
    value: typeof api[k].valor === "number"
      ? api[k].valor.toLocaleString("es-CL")
      : String(api[k].valor),
    delta: api[k].fecha || "",
    trend: "neutral",
  }));
}

function mapPipeline(api) {
  if (!api || !api.runs || !api.runs.length) return null;
  const latest = {};
  api.runs.forEach(r => { if (!latest[r.source]) latest[r.source] = r; });
  return latest;
}

function useLiveFeed() {
  const [feed, setFeed] = useState({ online: false });
  useEffect(() => {
    let alive = true;
    async function pull() {
      const [signals, macro, prices, realEstate, pstatus, cost] = await Promise.all([
        apiGet("/signals").catch(() => null),
        apiGet("/macro").catch(() => null),
        apiGet("/prices").catch(() => null),
        apiGet("/real-estate?limit=8").catch(() => null),
        apiGet("/pipeline/status").catch(() => null),
        apiGet("/cost").catch(() => null),
      ]);
      if (!alive) return;
      setFeed({
        online: !!(signals || macro || prices || realEstate || pstatus || cost),
        signals: mapSignals(signals),
        macro: mapMacro(macro),
        tickers: prices && prices.tickers && prices.tickers.length
          ? prices.tickers.map(t => ({
              key: t.key, name: t.name,
              price: typeof t.price === "number" ? t.price : 0,
              chg: typeof t.chg === "number" ? t.chg : 0,
            }))
          : null,
        realEstate: realEstate && realEstate.deals && realEstate.deals.length
          ? realEstate : null,
        pipeline: mapPipeline(pstatus),
        cost: cost || null,
      });
    }
    pull();
    const id = setInterval(pull, 30000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  return feed;
}

// ─── COMPONENTS ───────────────────────────────────────────────────────────────

const SignalBadge = ({ signal, urgency }) => {
  const cfg = {
    BULLISH: { color: "#00ff88", bg: "#00ff8815", label: "▲ BULLISH" },
    BEARISH: { color: "#ff4455", bg: "#ff445515", label: "▼ BEARISH" },
    WATCHLIST: { color: "#ffbb00", bg: "#ffbb0015", label: "◈ WATCH" },
    NEUTRAL: { color: "#888", bg: "#88888815", label: "— NEUTRAL" },
  }[signal] || { color: "#888", bg: "#88888815", label: signal };

  return (
    <span style={{
      color: cfg.color, background: cfg.bg,
      border: `1px solid ${cfg.color}40`,
      padding: "2px 8px", borderRadius: 2,
      fontSize: 10, fontWeight: 700, letterSpacing: 1,
      fontFamily: "'IBM Plex Mono', monospace",
    }}>
      {cfg.label}
      {urgency === "HIGH" && (
        <span style={{ color: "#ff4455", marginLeft: 4 }}>●</span>
      )}
    </span>
  );
};

const UrgencyDot = ({ urgency }) => {
  const color = { HIGH: "#ff4455", MEDIUM: "#ffbb00", LOW: "#444" }[urgency];
  return (
    <span style={{
      display: "inline-block", width: 6, height: 6,
      borderRadius: "50%", background: color,
      boxShadow: urgency === "HIGH" ? `0 0 6px ${color}` : "none",
      marginRight: 6, flexShrink: 0, marginTop: 1,
    }} />
  );
};

const MetricCard = ({ label, value, delta, trend }) => {
  const tcolor = trend === "up" ? "#00ff88"
    : trend === "down" ? "#ff4455" : "#888";
  return (
    <div style={{
      background: "#0d0d0d", border: "1px solid #1e1e1e",
      borderLeft: `2px solid ${tcolor}30`,
      padding: "10px 14px", borderRadius: 2,
    }}>
      <div style={{ color: "#555", fontSize: 9, letterSpacing: 2, marginBottom: 4,
        fontFamily: "'IBM Plex Mono', monospace", textTransform: "uppercase" }}>
        {label}
      </div>
      <div style={{ color: "#e8e8e8", fontSize: 15, fontWeight: 600,
        fontFamily: "'IBM Plex Mono', monospace" }}>
        {value}
      </div>
      <div style={{ color: tcolor, fontSize: 10, marginTop: 3,
        fontFamily: "'IBM Plex Mono', monospace" }}>
        {delta}
      </div>
    </div>
  );
};

const TickerBar = ({ tickers }) => (
  <div style={{
    background: "#050505", borderBottom: "1px solid #1a1a1a",
    padding: "6px 16px", display: "flex", gap: 24,
    overflowX: "auto", flexShrink: 0,
  }}>
    {tickers.map(t => (
      <div key={t.key} style={{ display: "flex", alignItems: "center",
        gap: 8, flexShrink: 0 }}>
        <span style={{ color: "#888", fontSize: 10, letterSpacing: 1,
          fontFamily: "'IBM Plex Mono', monospace" }}>
          {t.key}
        </span>
        <span style={{ color: "#e0e0e0", fontSize: 11, fontWeight: 600,
          fontFamily: "'IBM Plex Mono', monospace" }}>
          {t.price.toLocaleString("es-CL")}
        </span>
        <span style={{
          color: t.chg >= 0 ? "#00ff88" : "#ff4455",
          fontSize: 10, fontFamily: "'IBM Plex Mono', monospace",
        }}>
          {t.chg >= 0 ? "+" : ""}{t.chg.toFixed(2)}%
        </span>
      </div>
    ))}
    <div style={{ marginLeft: "auto", color: "#333", fontSize: 9,
      fontFamily: "'IBM Plex Mono', monospace", flexShrink: 0,
      alignSelf: "center" }}>
      BCS · RT DELAYED 15MIN
    </div>
  </div>
);

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: "#0a0a0a", border: "1px solid #2a2a2a",
      padding: "8px 12px", borderRadius: 2,
      fontFamily: "'IBM Plex Mono', monospace", fontSize: 11,
    }}>
      <div style={{ color: "#666", marginBottom: 4 }}>{label}</div>
      {payload.map(p => (
        <div key={p.name} style={{ color: p.color }}>
          {p.name}: {typeof p.value === "number"
            ? p.value.toLocaleString("es-CL")
            : p.value}
        </div>
      ))}
    </div>
  );
};

// ─── MAIN DASHBOARD ───────────────────────────────────────────────────────────

export default function SystematicFundDashboard() {
  const live = useLiveFeed();
  const signals = live.signals || CMF_SIGNALS;
  const macroData = live.macro || MACRO_DATA;

  const [activeSignal, setActiveSignal] = useState(CMF_SIGNALS[0]);
  const [activeTab, setActiveTab] = useState("signals");
  const [priceData, setPriceData] = useState(generatePriceSeries(892.3));
  const [carData] = useState(generateCAR());
  const [portfolioData] = useState(generatePortfolioHistory());
  const [tickers, setTickers] = useState(TICKERS);
  const [time, setTime] = useState(new Date());
  const [newSignal, setNewSignal] = useState(false);
  const blinkRef = useRef(null);

  // Señales en vivo: mantener la selección del usuario si sigue presente.
  useEffect(() => {
    if (!live.signals || !live.signals.length) return;
    setActiveSignal(prev =>
      prev && live.signals.some(s => s.id === prev.id) ? prev : live.signals[0]
    );
  }, [live.signals]);

  // Tickers en vivo desde /prices (la animación de abajo agrega jitter).
  useEffect(() => {
    if (live.tickers) setTickers(live.tickers);
  }, [live.tickers]);

  // Historial de precio del ticker de la señal activa (cae a sintético).
  useEffect(() => {
    let alive = true;
    const key = String((activeSignal && activeSignal.empresa) || "ipsa")
      .toLowerCase().replace(/[^a-z]/g, "").slice(0, 12) || "ipsa";
    apiGet("/prices/" + key + "/history?days=120")
      .then(d => { if (alive && d && d.series && d.series.length) setPriceData(d.series); })
      .catch(() => {});
    return () => { alive = false; };
  }, [activeSignal]);

  useEffect(() => {
    const interval = setInterval(() => {
      setTime(new Date());
      setTickers(prev => prev.map(t => ({
        ...t,
        price: parseFloat((t.price * (1 + (Math.random() - 0.5) * 0.0008)).toFixed(2)),
        chg: parseFloat((t.chg + (Math.random() - 0.5) * 0.05).toFixed(2)),
      })));
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const timeout = setTimeout(() => {
      setNewSignal(true);
      blinkRef.current = setTimeout(() => setNewSignal(false), 4000);
    }, 8000);
    return () => {
      clearTimeout(timeout);
      if (blinkRef.current) clearTimeout(blinkRef.current);
    };
  }, []);

  const portfolioReturn = portfolioData.length > 1
    ? ((portfolioData.at(-1).portfolio / portfolioData[0].portfolio - 1) * 100).toFixed(2)
    : "0.00";
  const benchReturn = portfolioData.length > 1
    ? ((portfolioData.at(-1).benchmark / portfolioData[0].benchmark - 1) * 100).toFixed(2)
    : "0.00";
  const alpha = (parseFloat(portfolioReturn) - parseFloat(benchReturn)).toFixed(2);

  const reComuna = (live.realEstate && live.realEstate.comuna) || "Las Condes";
  const reDeals = (live.realEstate && live.realEstate.deals) || [
    { title: "Depto 78m² Av. Apoquindo", uf: 5840,  uf_m2: 74.9,  delta_pct: -12 },
    { title: "Depto 55m² Las Urbinas",   uf: 4290,  uf_m2: 78.0,  delta_pct: -8 },
    { title: "Depto 120m² El Golf",      uf: 12100, uf_m2: 100.8, delta_pct: 6 },
    { title: "Depto 65m² Manquehue",     uf: 5525,  uf_m2: 85.0,  delta_pct: -3 },
  ];

  const PIPE_SAMPLE = [
    { name: "CMF Hechos Esenciales", status: "LIVE", last: "hace 12 min", count: 5, color: "#00ff88" },
    { name: "BCCh Macro Series",     status: "LIVE", last: "hace 2h",     count: 7, color: "#00ff88" },
    { name: "Portal Inmobiliario",   status: "DEMO", last: "modo demo",   count: 20, color: "#ffbb00" },
    { name: "BCS Volumen",           status: "PENDING", last: "en construcción", count: 0, color: "#444" },
    { name: "Job Postings",          status: "PENDING", last: "en construcción", count: 0, color: "#444" },
    { name: "Diario Oficial NLP",    status: "PENDING", last: "en construcción", count: 0, color: "#444" },
  ];
  const pipeColor = st => st === "OK" ? "#00ff88" : st === "ERROR" ? "#ff4455" : "#ffbb00";
  const pipeRows = live.pipeline
    ? [["CMF", "CMF Hechos Esenciales"], ["BCCH", "BCCh Macro Series"], ["RE", "Portal Inmobiliario"]]
        .map(([k, name]) => {
          const r = live.pipeline[k];
          return r
            ? { name, status: r.status === "OK" ? "LIVE" : r.status,
                last: String(r.started_at || "").slice(11, 16) || "—",
                count: r.records || 0, color: pipeColor(r.status) }
            : { name, status: "—", last: "sin datos", count: 0, color: "#444" };
        })
        .concat(PIPE_SAMPLE.slice(3))
    : PIPE_SAMPLE;

  const fmtK = n => n >= 1000 ? (n / 1000).toFixed(0) + "K" : String(n);
  const COST_SAMPLE = [
    { label: "TOKENS USADOS",  value: "284K",  sub: "de ~857K/mes" },
    { label: "COSTO API HOY",  value: "$0.08", sub: "Haiku + Sonnet" },
    { label: "PROYECCIÓN MES", value: "$2.40", sub: "con caching" },
    { label: "MODELO MIX",     value: "80/20", sub: "Haiku/Sonnet" },
  ];
  const costCards = live.cost
    ? [
        { label: "TOKENS USADOS",     value: fmtK(live.cost.estimated_tokens || 0), sub: `${live.cost.total_signals_processed || 0} señales` },
        { label: "COSTO ESTIMADO",    value: "$" + (live.cost.estimated_cost_usd || 0), sub: "Haiku + Sonnet" },
        { label: "HECHOS PROCESADOS", value: fmtK(live.cost.total_hechos_scraped || 0), sub: "CMF" },
        { label: "MODELO MIX",        value: "80/20", sub: "Haiku/Sonnet" },
      ]
    : COST_SAMPLE;

  return (
    <div style={{
      background: "#070707", color: "#c8c8c8", minHeight: "100vh",
      fontFamily: "'IBM Plex Mono', monospace",
      display: "flex", flexDirection: "column",
    }}>
      {/* Google Font */}
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600;700&display=swap');
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: #0a0a0a; }
        ::-webkit-scrollbar-thumb { background: #2a2a2a; }
        @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.3} }
        @keyframes slideIn { from{transform:translateY(-8px);opacity:0} to{transform:translateY(0);opacity:1} }
        @keyframes pulse { 0%,100%{box-shadow:0 0 0 0 #ff445540} 50%{box-shadow:0 0 0 6px #ff445500} }
      `}</style>

      {/* ── HEADER ── */}
      <div style={{
        background: "#050505", borderBottom: "1px solid #1a1a1a",
        padding: "10px 20px", display: "flex",
        alignItems: "center", justifyContent: "space-between",
        flexShrink: 0,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ color: "#00ff88", fontWeight: 700, fontSize: 13,
            letterSpacing: 2 }}>
            SYS▸FUND
          </div>
          <div style={{ color: "#2a2a2a", fontSize: 12 }}>|</div>
          <div style={{ color: "#555", fontSize: 10, letterSpacing: 1 }}>
            CHILE · MULTI-STRATEGY SYSTEMATIC
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <div style={{ textAlign: "right" }}>
            <div style={{ color: "#555", fontSize: 9, letterSpacing: 2 }}>
              AUM TARGET
            </div>
            <div style={{ color: "#e0e0e0", fontSize: 12, fontWeight: 600 }}>
              USD 1.0M
            </div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div style={{ color: "#555", fontSize: 9, letterSpacing: 2 }}>
              ALPHA 6M
            </div>
            <div style={{
              color: alpha > 0 ? "#00ff88" : "#ff4455",
              fontSize: 12, fontWeight: 600,
            }}>
              {alpha > 0 ? "+" : ""}{alpha}%
            </div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div style={{ color: "#555", fontSize: 9, letterSpacing: 2 }}>
              SIGNALS
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{
                color: "#ff4455", fontSize: 12, fontWeight: 700,
                animation: newSignal ? "blink 0.5s 6" : "none",
              }}>
                {signals.filter(s => s.urgency === "HIGH").length}
              </span>
              <span style={{ color: "#555", fontSize: 12 }}>HIGH</span>
            </div>
          </div>
          <div style={{ color: "#444", fontSize: 11 }}>
            {time.toLocaleTimeString("es-CL")}
          </div>
          <div title={live.online ? "API conectada" : "API offline — datos de muestra"}
            style={{
              width: 8, height: 8, borderRadius: "50%",
              background: live.online ? "#00ff88" : "#ffbb00",
              boxShadow: `0 0 8px ${live.online ? "#00ff88" : "#ffbb00"}`,
              animation: "pulse 2s infinite",
            }} />
        </div>
      </div>

      {/* ── TICKER BAR ── */}
      <TickerBar tickers={tickers} />

      {/* ── NAV TABS ── */}
      <div style={{
        background: "#050505", borderBottom: "1px solid #1a1a1a",
        padding: "0 20px", display: "flex", gap: 0, flexShrink: 0,
      }}>
        {[
          { id: "signals", label: "◈ SIGNALS" },
          { id: "portfolio", label: "▦ PORTFOLIO" },
          { id: "macro", label: "◎ MACRO" },
          { id: "altdata", label: "⬡ ALT DATA" },
        ].map(tab => (
          <button key={tab.id} onClick={() => setActiveTab(tab.id)}
            style={{
              background: "none", border: "none",
              borderBottom: activeTab === tab.id ? "2px solid #00ff88" : "2px solid transparent",
              color: activeTab === tab.id ? "#00ff88" : "#555",
              padding: "10px 16px", cursor: "pointer",
              fontSize: 10, letterSpacing: 2, fontWeight: 600,
              fontFamily: "'IBM Plex Mono', monospace",
              transition: "color 0.2s",
            }}>
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── MAIN CONTENT ── */}
      <div style={{ flex: 1, overflow: "hidden", display: "flex", minHeight: 0 }}>

        {/* ═══ SIGNALS TAB ═══ */}
        {activeTab === "signals" && (
          <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>

            {/* Signal List */}
            <div style={{
              width: 340, borderRight: "1px solid #1a1a1a",
              overflowY: "auto", flexShrink: 0,
            }}>
              <div style={{
                padding: "10px 16px", borderBottom: "1px solid #1a1a1a",
                display: "flex", justifyContent: "space-between",
                alignItems: "center",
              }}>
                <span style={{ color: "#555", fontSize: 9, letterSpacing: 2 }}>
                  CMF FEED — HECHOS ESENCIALES
                </span>
                <span style={{ color: "#333", fontSize: 9 }}>
                  {signals.length} HOY
                </span>
              </div>

              {signals.map((sig, i) => (
                <div key={sig.id}
                  onClick={() => setActiveSignal(sig)}
                  style={{
                    padding: "14px 16px",
                    borderBottom: "1px solid #111",
                    cursor: "pointer",
                    background: activeSignal?.id === sig.id ? "#0d0d0d" : "transparent",
                    borderLeft: activeSignal?.id === sig.id
                      ? "2px solid #00ff88" : "2px solid transparent",
                    animation: i === 0 && newSignal ? "slideIn 0.3s ease" : "none",
                    transition: "background 0.15s",
                  }}>
                  <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "flex-start", marginBottom: 8 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <UrgencyDot urgency={sig.urgency} />
                      <span style={{ color: "#e0e0e0", fontSize: 12, fontWeight: 600 }}>
                        {sig.empresa}
                      </span>
                    </div>
                    <span style={{ color: "#444", fontSize: 9 }}>{sig.time}</span>
                  </div>
                  <div style={{ marginBottom: 8 }}>
                    <SignalBadge signal={sig.signal} urgency={sig.urgency} />
                    <span style={{ color: "#444", fontSize: 9, marginLeft: 8,
                      letterSpacing: 1 }}>
                      {sig.cat}
                    </span>
                  </div>
                  <div style={{ color: "#666", fontSize: 10, lineHeight: 1.5 }}>
                    {sig.tipo}
                  </div>
                  <div style={{
                    marginTop: 6,
                    background: "#0a0a0a", border: "1px solid #1a1a1a",
                    padding: "4px 8px", borderRadius: 2, fontSize: 10, color: "#888",
                  }}>
                    {sig.action}
                  </div>
                </div>
              ))}
            </div>

            {/* Signal Detail + Chart */}
            <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
              {activeSignal && (
                <div style={{ animation: "slideIn 0.2s ease" }}>
                  {/* Header */}
                  <div style={{ marginBottom: 20 }}>
                    <div style={{ display: "flex", alignItems: "center",
                      gap: 12, marginBottom: 8 }}>
                      <h2 style={{ color: "#fff", fontSize: 20, fontWeight: 700,
                        margin: 0 }}>
                        {activeSignal.empresa}
                      </h2>
                      <SignalBadge signal={activeSignal.signal}
                        urgency={activeSignal.urgency} />
                      <span style={{
                        color: "#666", fontSize: 10,
                        background: "#111", border: "1px solid #222",
                        padding: "2px 8px", borderRadius: 2,
                      }}>
                        CONF {(activeSignal.conf * 100).toFixed(0)}%
                      </span>
                    </div>
                    <div style={{ color: "#555", fontSize: 11 }}>
                      {activeSignal.tipo} · {activeSignal.time} · CMF Hecho Esencial
                    </div>
                  </div>

                  {/* Action Box */}
                  <div style={{
                    background: "#0a0a0a",
                    border: "1px solid #1e1e1e",
                    borderLeft: "3px solid #00ff88",
                    padding: "14px 16px", borderRadius: 2, marginBottom: 20,
                  }}>
                    <div style={{ color: "#555", fontSize: 9, letterSpacing: 2,
                      marginBottom: 6 }}>
                      ACCIÓN RECOMENDADA
                    </div>
                    <div style={{ color: "#00ff88", fontSize: 13, fontWeight: 600 }}>
                      {activeSignal.action}
                    </div>
                    <div style={{ color: "#888", fontSize: 11, marginTop: 8,
                      lineHeight: 1.6 }}>
                      {activeSignal.reasoning}
                    </div>
                  </div>

                  {/* Price Chart */}
                  <div style={{ marginBottom: 20 }}>
                    <div style={{ color: "#555", fontSize: 9, letterSpacing: 2,
                      marginBottom: 12 }}>
                      PRECIO — 120D · {activeSignal.empresa} CI EQUITY
                    </div>
                    <ResponsiveContainer width="100%" height={180}>
                      <AreaChart data={priceData}>
                        <defs>
                          <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor="#00ff88" stopOpacity={0.15} />
                            <stop offset="95%" stopColor="#00ff88" stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <XAxis dataKey="date" tick={{ fill: "#333", fontSize: 9 }}
                          tickLine={false} axisLine={false}
                          interval={Math.floor(priceData.length / 6)} />
                        <YAxis tick={{ fill: "#333", fontSize: 9 }}
                          tickLine={false} axisLine={false}
                          tickFormatter={v => v.toLocaleString("es-CL")} />
                        <Tooltip content={<CustomTooltip />} />
                        <Area type="monotone" dataKey="price"
                          stroke="#00ff88" strokeWidth={1.5}
                          fill="url(#priceGrad)" dot={false} />
                        <ReferenceLine
                          x={priceData[priceData.length - 6]?.date}
                          stroke="#ff4455" strokeWidth={1}
                          strokeDasharray="3 3" label={{
                            value: "SEÑAL", fill: "#ff4455",
                            fontSize: 8, fontFamily: "'IBM Plex Mono', monospace",
                          }} />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>

                  {/* CAR Chart */}
                  <div>
                    <div style={{ color: "#555", fontSize: 9, letterSpacing: 2,
                      marginBottom: 12 }}>
                      CAR — CUMULATIVE ABNORMAL RETURN (SIMULADO)
                    </div>
                    <ResponsiveContainer width="100%" height={140}>
                      <BarChart data={carData} barSize={6}>
                        <XAxis dataKey="day" tick={{ fill: "#333", fontSize: 9 }}
                          tickLine={false} axisLine={{ stroke: "#1a1a1a" }}
                          label={{ value: "Días desde evento", fill: "#444",
                            fontSize: 8, position: "insideBottom", offset: -2,
                            fontFamily: "'IBM Plex Mono', monospace" }} />
                        <YAxis tick={{ fill: "#333", fontSize: 9 }}
                          tickLine={false} axisLine={false}
                          tickFormatter={v => `${v.toFixed(1)}%`} />
                        <Tooltip content={<CustomTooltip />} />
                        <ReferenceLine y={0} stroke="#2a2a2a" />
                        <Bar dataKey="car" name="CAR %"
                          fill="#00ff88" opacity={0.8}
                          radius={[1, 1, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ═══ PORTFOLIO TAB ═══ */}
        {activeTab === "portfolio" && (
          <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>

            {/* Stats Row */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)",
              gap: 12, marginBottom: 24 }}>
              {[
                { label: "PORTFOLIO 6M", value: `+${portfolioReturn}%`,
                  color: "#00ff88" },
                { label: "BENCHMARK IPSA", value: `+${benchReturn}%`,
                  color: "#888" },
                { label: "ALPHA GENERADO", value: `+${alpha}%`,
                  color: alpha > 0 ? "#00ff88" : "#ff4455" },
                { label: "SHARPE RATIO", value: "1.74", color: "#ffbb00" },
              ].map(stat => (
                <div key={stat.label} style={{
                  background: "#0a0a0a", border: "1px solid #1a1a1a",
                  padding: "16px", borderRadius: 2,
                }}>
                  <div style={{ color: "#555", fontSize: 9, letterSpacing: 2,
                    marginBottom: 8 }}>
                    {stat.label}
                  </div>
                  <div style={{ color: stat.color, fontSize: 22, fontWeight: 700 }}>
                    {stat.value}
                  </div>
                </div>
              ))}
            </div>

            {/* Portfolio Chart */}
            <div style={{ background: "#0a0a0a", border: "1px solid #1a1a1a",
              borderRadius: 2, padding: 16, marginBottom: 24 }}>
              <div style={{ color: "#555", fontSize: 9, letterSpacing: 2,
                marginBottom: 16 }}>
                PORTFOLIO vs BENCHMARK — 6M
              </div>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={portfolioData}>
                  <XAxis dataKey="date" tick={{ fill: "#333", fontSize: 9 }}
                    tickLine={false} axisLine={false}
                    interval={Math.floor(portfolioData.length / 8)} />
                  <YAxis tick={{ fill: "#333", fontSize: 9 }}
                    tickLine={false} axisLine={false}
                    tickFormatter={v => `$${(v / 1000).toFixed(0)}K`} />
                  <Tooltip content={<CustomTooltip />} />
                  <Line type="monotone" dataKey="portfolio" name="Portfolio"
                    stroke="#00ff88" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="benchmark" name="IPSA"
                    stroke="#444" strokeWidth={1} dot={false}
                    strokeDasharray="4 2" />
                </LineChart>
              </ResponsiveContainer>
            </div>

            {/* Allocation */}
            <div style={{ background: "#0a0a0a", border: "1px solid #1a1a1a",
              borderRadius: 2, padding: 16 }}>
              <div style={{ color: "#555", fontSize: 9, letterSpacing: 2,
                marginBottom: 16 }}>
                ALLOCATION — RISK BUDGET
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)",
                gap: 10 }}>
                {[
                  { name: "Trend Following (LSTM)", alloc: 35, risk: 38, pnl: "+4.2%" },
                  { name: "Real Estate Systematic", alloc: 25, risk: 22, pnl: "+2.8%" },
                  { name: "Alt Data L/S", alloc: 20, risk: 25, pnl: "+1.9%" },
                  { name: "Macro Overlay / Cash", alloc: 20, risk: 15, pnl: "-0.4%" },
                ].map(layer => (
                  <div key={layer.name} style={{
                    background: "#070707", border: "1px solid #1a1a1a",
                    padding: "12px 14px", borderRadius: 2,
                  }}>
                    <div style={{ display: "flex", justifyContent: "space-between",
                      marginBottom: 8 }}>
                      <span style={{ color: "#888", fontSize: 10 }}>{layer.name}</span>
                      <span style={{
                        color: layer.pnl.startsWith("+") ? "#00ff88" : "#ff4455",
                        fontSize: 11, fontWeight: 600,
                      }}>
                        {layer.pnl}
                      </span>
                    </div>
                    <div style={{ display: "flex", gap: 12 }}>
                      <div>
                        <div style={{ color: "#444", fontSize: 9 }}>ALLOC</div>
                        <div style={{ color: "#e0e0e0", fontSize: 13,
                          fontWeight: 600 }}>
                          {layer.alloc}%
                        </div>
                      </div>
                      <div>
                        <div style={{ color: "#444", fontSize: 9 }}>RISK</div>
                        <div style={{ color: "#ffbb00", fontSize: 13,
                          fontWeight: 600 }}>
                          {layer.risk}%
                        </div>
                      </div>
                    </div>
                    <div style={{ marginTop: 8, background: "#111",
                      borderRadius: 1, height: 2 }}>
                      <div style={{
                        height: "100%", borderRadius: 1,
                        width: `${layer.alloc}%`,
                        background: "#00ff8860",
                      }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* ═══ MACRO TAB ═══ */}
        {activeTab === "macro" && (
          <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)",
              gap: 12, marginBottom: 24 }}>
              {macroData.map(m => (
                <MetricCard key={m.label} {...m} />
              ))}
            </div>

            {/* BCCh Scenario */}
            <div style={{ background: "#0a0a0a", border: "1px solid #1a1a1a",
              borderRadius: 2, padding: 16, marginBottom: 16 }}>
              <div style={{ color: "#555", fontSize: 9, letterSpacing: 2,
                marginBottom: 16 }}>
                ESCENARIOS TPM — PRÓXIMA REUNIÓN BCCh
              </div>
              <div style={{ display: "grid",
                gridTemplateColumns: "repeat(3, 1fr)", gap: 10 }}>
                {[
                  { label: "RECORTE 25 bps", prob: 45, tpm: "4.75%",
                    impact: "CLP débil, bonos largas ↑", color: "#00ff88" },
                  { label: "HOLD", prob: 40, tpm: "5.00%",
                    impact: "Status quo. CLP neutral", color: "#ffbb00" },
                  { label: "HIKE 25 bps", prob: 15, tpm: "5.25%",
                    impact: "CLP fuerte, equities ↓", color: "#ff4455" },
                ].map(esc => (
                  <div key={esc.label} style={{
                    background: "#070707",
                    border: `1px solid ${esc.color}30`,
                    padding: "14px", borderRadius: 2,
                  }}>
                    <div style={{ color: esc.color, fontSize: 10,
                      fontWeight: 700, marginBottom: 8 }}>
                      {esc.label}
                    </div>
                    <div style={{ color: "#fff", fontSize: 22,
                      fontWeight: 700, marginBottom: 4 }}>
                      {esc.prob}%
                    </div>
                    <div style={{ color: "#444", fontSize: 9,
                      marginBottom: 8 }}>
                      PROB IMPLÍCITA
                    </div>
                    <div style={{ color: "#888", fontSize: 10,
                      lineHeight: 1.5 }}>
                      TPM → {esc.tpm}
                    </div>
                    <div style={{ color: "#555", fontSize: 9,
                      marginTop: 6, lineHeight: 1.5 }}>
                      {esc.impact}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Cross-asset */}
            <div style={{ background: "#0a0a0a", border: "1px solid #1a1a1a",
              borderRadius: 2, padding: 16 }}>
              <div style={{ color: "#555", fontSize: 9, letterSpacing: 2,
                marginBottom: 14 }}>
                CROSS-ASSET SIGNALS
              </div>
              {[
                { asset: "BONO BCCh 10Y", signal: "SHORT",
                  color: "#ff4455", reason: "Curva re-steepening. Inflación persistente." },
                { asset: "USD/CLP", signal: "SHORT CLP",
                  color: "#ff4455", reason: "Cobre -8% último mes. Presión exportaciones." },
                { asset: "IPSA Small Cap", signal: "LONG",
                  color: "#00ff88", reason: "Descuento vs Large Cap en máximos 3Y." },
                { asset: "Real Estate Santiago", signal: "NEUTRAL",
                  color: "#888", reason: "UF/m² estabilizando. Supply pipeline normalizing." },
              ].map(row => (
                <div key={row.asset} style={{
                  display: "flex", alignItems: "flex-start",
                  gap: 14, padding: "10px 0",
                  borderBottom: "1px solid #111",
                }}>
                  <span style={{ color: row.color, fontSize: 9,
                    fontWeight: 700, width: 80, flexShrink: 0,
                    letterSpacing: 1, paddingTop: 2 }}>
                    {row.signal}
                  </span>
                  <span style={{ color: "#888", fontSize: 10,
                    width: 160, flexShrink: 0 }}>
                    {row.asset}
                  </span>
                  <span style={{ color: "#555", fontSize: 10,
                    lineHeight: 1.5 }}>
                    {row.reason}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ═══ ALT DATA TAB ═══ */}
        {activeTab === "altdata" && (
          <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>

            {/* Pipeline Status */}
            <div style={{ background: "#0a0a0a", border: "1px solid #1a1a1a",
              borderRadius: 2, padding: 16, marginBottom: 20 }}>
              <div style={{ color: "#555", fontSize: 9, letterSpacing: 2,
                marginBottom: 14 }}>
                PIPELINE STATUS — ALT DATA
              </div>
              <div style={{ display: "grid",
                gridTemplateColumns: "repeat(3, 1fr)", gap: 10 }}>
                {pipeRows.map(src => (
                  <div key={src.name} style={{
                    background: "#070707",
                    border: `1px solid ${src.color}25`,
                    padding: "12px 14px", borderRadius: 2,
                  }}>
                    <div style={{ display: "flex", justifyContent: "space-between",
                      marginBottom: 8 }}>
                      <span style={{ color: src.color, fontSize: 9,
                        fontWeight: 700, letterSpacing: 1 }}>
                        {src.status}
                      </span>
                      {src.count > 0 && (
                        <span style={{ color: "#555", fontSize: 9 }}>
                          {src.count} items
                        </span>
                      )}
                    </div>
                    <div style={{ color: "#888", fontSize: 10,
                      lineHeight: 1.5, marginBottom: 6 }}>
                      {src.name}
                    </div>
                    <div style={{ color: "#444", fontSize: 9 }}>
                      {src.last}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Cost Monitor */}
            <div style={{ background: "#0a0a0a", border: "1px solid #1a1a1a",
              borderRadius: 2, padding: 16, marginBottom: 20 }}>
              <div style={{ color: "#555", fontSize: 9, letterSpacing: 2,
                marginBottom: 14 }}>
                API COST MONITOR — MES ACTUAL
              </div>
              <div style={{ display: "grid",
                gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
                {costCards.map(m => (
                  <div key={m.label} style={{
                    background: "#070707", border: "1px solid #1a1a1a",
                    padding: "12px 14px", borderRadius: 2,
                  }}>
                    <div style={{ color: "#555", fontSize: 9,
                      letterSpacing: 1, marginBottom: 8 }}>
                      {m.label}
                    </div>
                    <div style={{ color: "#e0e0e0", fontSize: 16,
                      fontWeight: 700 }}>
                      {m.value}
                    </div>
                    <div style={{ color: "#444", fontSize: 9, marginTop: 4 }}>
                      {m.sub}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Real Estate Feed */}
            <div style={{ background: "#0a0a0a", border: "1px solid #1a1a1a",
              borderRadius: 2, padding: 16 }}>
              <div style={{ color: "#555", fontSize: 9, letterSpacing: 2,
                marginBottom: 14 }}>
                PORTAL INMOBILIARIO — TOP DEALS · {reComuna.toUpperCase()}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {reDeals.map((prop, i) => (
                  <div key={i} style={{
                    display: "flex", justifyContent: "space-between",
                    alignItems: "center",
                    padding: "10px 12px",
                    background: "#070707", border: "1px solid #111",
                    borderRadius: 2,
                  }}>
                    <span style={{ color: "#888", fontSize: 10,
                      flex: 1 }}>
                      {prop.title}
                    </span>
                    <span style={{ color: "#e0e0e0", fontSize: 11,
                      fontWeight: 600, width: 100, textAlign: "right" }}>
                      UF {prop.uf.toLocaleString("es-CL")}
                    </span>
                    <span style={{ color: "#666", fontSize: 10,
                      width: 90, textAlign: "right" }}>
                      UF/m² {prop.uf_m2}
                    </span>
                    <span style={{
                      color: prop.delta_pct <= 0 ? "#00ff88" : "#ff4455",
                      fontSize: 10, width: 120, textAlign: "right",
                      fontWeight: 600,
                    }}>
                      {prop.delta_pct > 0 ? "+" : ""}{prop.delta_pct}% vs zona
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── STATUS BAR ── */}
      <div style={{
        background: "#050505", borderTop: "1px solid #1a1a1a",
        padding: "6px 20px", display: "flex",
        justifyContent: "space-between", alignItems: "center",
        flexShrink: 0,
      }}>
        <div style={{ display: "flex", gap: 20 }}>
          <span style={{ color: "#333", fontSize: 9 }}>
            BLOOMBERG · BLPAPI 3.19
          </span>
          <span style={{ color: "#333", fontSize: 9 }}>
            DUCKDB · LOCAL
          </span>
          <span style={{ color: "#333", fontSize: 9 }}>
            CHROMADB · v0.5
          </span>
        </div>
        <div style={{ color: "#1e1e1e", fontSize: 9, letterSpacing: 1 }}>
          SYSTEMATIC FUND INFRASTRUCTURE v0.1 · JAVIER BASTIAS HEREDIA
        </div>
      </div>
    </div>
  );
}
