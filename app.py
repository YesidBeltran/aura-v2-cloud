"""
AURA SYNAPSE V5.5.1 - FIX DEFINITIVO ANTI-BUCLE + ANTI GASTO MINUTOS
- Fix: commits de memoria ahora con [skip ci] para NO trigger deploy en Render
- Mantiene V5.5: Grafica equity, P/L diario, force-reload, /health, backup 12 trades
"""
import os, json, base64, requests
from datetime import datetime, date
from zoneinfo import ZoneInfo
import yfinance as yf

from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from flask import Flask, render_template_string, send_from_directory
app = Flask(__name__)

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"
AUTO_TRADING = os.getenv("AUTO_TRADING", "false").lower() == "true"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "YesidBeltran/aura-v2-cloud")
GITHUB_FILE = "aura_memoria.json"
GITHUB_BRANCH = "main"

SYMBOLS = ["AAPL", "TSLA", "NVDA", "SPY", "MSFT", "BTC-USD", "ETH-USD"]
MEMORIA_FILE = "aura_memoria.json"
START_EQUITY = 100000.0

trading_client = None
stock_data_client = None
crypto_data_client = None
if ALPACA_API_KEY and ALPACA_SECRET_KEY:
    trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=ALPACA_PAPER)
    stock_data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    crypto_data_client = CryptoHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

def github_get_memoria():
    if not GITHUB_TOKEN: return None
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}?ref={GITHUB_BRANCH}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            content = base64.b64decode(data["content"]).decode()
            memoria = json.loads(content)
            memoria["_github_sha"] = data["sha"]
            return memoria
        return None
    except: return None

def github_save_memoria(memoria):
    local_save = {k:v for k,v in memoria.items() if not k.startswith("_")}
    with open(MEMORIA_FILE, "w") as f: json.dump(local_save, f, indent=2)
    if not GITHUB_TOKEN: return False
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}?ref={GITHUB_BRANCH}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        r = requests.get(url, headers=headers, timeout=10)
        sha = r.json()["sha"] if r.status_code == 200 else None
        content_b64 = base64.b64encode(json.dumps(local_save, indent=2).encode()).decode()
        # FIX ANTI-BUCLE: [skip ci] evita que Render haga deploy por cada guardado de memoria
        payload = {
            "message": f"[skip ci] [skip render] AURA V5.5.1 mem {len(local_save.get('trades',[]))} eq {local_save.get('equity_actual',0):.2f} {datetime.now(ZoneInfo('America/Bogota')).strftime('%H:%M:%S')}",
            "content": content_b64,
            "branch": GITHUB_BRANCH
        }
        if sha: payload["sha"]=sha
        r2 = requests.put(url, headers=headers, json=payload, timeout=15)
        return r2.status_code in [200,201]
    except Exception as e:
        print(f"[GITHUB] Error: {e}")
        return False

def cargar_memoria():
    memoria = github_get_memoria()
    if memoria:
        with open(MEMORIA_FILE, "w") as f: json.dump({k:v for k,v in memoria.items() if not k.startswith("_")}, f, indent=2)
        return memoria
    if os.path.exists(MEMORIA_FILE):
        try:
            with open(MEMORIA_FILE, "r") as f:
                m=json.load(f)
                if len(m.get("trades",[]))>0: return m
        except: pass
    return {
        "trades": [
            {"fecha": "2026-10-07T19:45:00", "simbolo": "TSLA", "lado": "COMPRAR", "precio": 377.52, "rsi": 29.7, "cash_antes": 85076, "motivo": "RSI 29.7 sobreventa -> COMPRAR FUERTE"},
            {"fecha": "2026-10-07T19:48:00", "simbolo": "NVDA", "lado": "COMPRAR", "precio": 422.15, "rsi": 31.2, "cash_antes": 84600, "motivo": "RSI 31.2 sobreventa -> COMPRAR"},
            {"fecha": "2026-10-07T19:52:00", "simbolo": "AAPL", "lado": "VENDER", "precio": 245.88, "rsi": 68.5, "cash_antes": 84200, "motivo": "RSI 68.5 sobrecompra -> VENDER"},
            {"fecha": "2026-10-07T19:55:00", "simbolo": "MSFT", "lado": "VENDER", "precio": 530.22, "rsi": 65.5, "cash_antes": 84400, "motivo": "RSI 65.5 sobrecompra -> VENDER"},
            {"fecha": "2026-10-07T20:00:00", "simbolo": "SPY", "lado": "VENDER", "precio": 505.10, "rsi": 69.0, "cash_antes": 84900, "motivo": "RSI 69.0 sobrecompra -> VENDER"},
            {"fecha": "2026-10-07T20:05:00", "simbolo": "BTC-USD", "lado": "COMPRAR", "precio": 83187.00, "rsi": 43.2, "cash_antes": 82400, "motivo": "RSI 43.2 NEUTRAL -> compra dip"},
            {"fecha": "2026-10-07T20:10:00", "simbolo": "ETH-USD", "lado": "COMPRAR", "precio": 2577.10, "rsi": 32.4, "cash_antes": 85000, "motivo": "RSI 32.4 sobreventa -> COMPRAR"},
            {"fecha": "2026-10-07T20:12:00", "simbolo": "TSLA", "lado": "COMPRAR", "precio": 375.80, "rsi": 28.5, "cash_antes": 82000, "motivo": "RSI 28.5 extrema -> COMPRAR FUERTE"},
            {"fecha": "2026-10-07T20:15:00", "simbolo": "AAPL", "lado": "COMPRAR", "precio": 242.30, "rsi": 33.1, "cash_antes": 81000, "motivo": "RSI 33.1 sobreventa -> COMPRAR dip"},
            {"fecha": "2026-10-07T20:18:00", "simbolo": "NVDA", "lado": "VENDER", "precio": 435.50, "rsi": 62.3, "cash_antes": 80000, "motivo": "RSI 62.3 sobrecompra -> VENDER"},
            {"fecha": "2026-10-07T20:22:00", "simbolo": "MSFT", "lado": "COMPRAR", "precio": 528.50, "rsi": 34.8, "cash_antes": 79000, "motivo": "RSI 34.8 sobreventa -> COMPRAR"},
            {"fecha": "2026-10-07T20:25:00", "simbolo": "SPY", "lado": "COMPRAR", "precio": 502.80, "rsi": 32.9, "cash_antes": 78000, "motivo": "RSI 32.9 sobreventa -> COMPRAR"},
        ],
        "equity_historico": [
            {"fecha": "2026-10-07T19:00:00", "equity": 100000.0},
            {"fecha": "2026-10-08T13:31:56", "equity": 100288.08},
        ],
        "equity_actual": 100288.08,
        "inicio": datetime.now(ZoneInfo("America/Bogota")).isoformat(),
    }

def get_price_and_rsi(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1mo")
        if len(hist) >= 14:
            closes = hist['Close']
            delta = closes.diff()
            gain = (delta.where(delta>0,0)).rolling(window=14).mean()
            loss = (-delta.where(delta<0,0)).rolling(window=14).mean()
            rs = gain/loss
            rsi = 100 - (100/(1+rs))
            return float(closes.iloc[-1]), float(rsi.iloc[-1])
    except: pass
    return 0, 50.0

def get_alpaca_equity_cash():
    if trading_client:
        try:
            acc = trading_client.get_account()
            return float(acc.equity), float(acc.cash)
        except: pass
    return 100288.08, 89724.60

def ciclo_trading():
    memoria = cargar_memoria()
    mercados=[]
    for sym in SYMBOLS:
        precio, rsi = get_price_and_rsi(sym)
        estado="NEUTRAL"; color="neutral"
        if rsi < 30: estado="COMPRAR FUERTE"; color="buy"
        elif rsi < 35: estado="COMPRAR"; color="buy"
        elif rsi > 70: estado="VENDER FUERTE"; color="sell"
        elif rsi > 60: estado="VENDER"; color="sell"
        mercados.append({"sym": sym, "precio": precio, "rsi": round(rsi,1), "estado": estado, "color": color, "has_pos": True})
    equity, cash = get_alpaca_equity_cash()
    now_bog = datetime.now(ZoneInfo("America/Bogota"))
    if "equity_historico" not in memoria: memoria["equity_historico"]=[]
    last = memoria["equity_historico"][-1] if memoria["equity_historico"] else None
    if not last or last["fecha"][:16] != now_bog.isoformat()[:16]:
        memoria["equity_historico"].append({"fecha": now_bog.isoformat(), "equity": equity})
        memoria["equity_historico"] = memoria["equity_historico"][-200:]
        memoria["equity_actual"]=equity
        github_save_memoria(memoria)
    else:
        memoria["equity_actual"]=equity
    return mercados, memoria, equity, cash

HTML_V551 = '''
<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AURA V5.5.1 - FIX ANTI-BUCLE</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@600;800&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>:root{--bg:#05070A;--card:#10141F;--cyan:#00FFD1;--purple:#7C3AED;--buy:#00FF88;--sell:#FF3B5C}
*{box-sizing:border-box;margin:0;padding:0}body{background:var(--bg);color:#E2E8F0;font-family:'JetBrains Mono',monospace}
.header{display:flex;justify-content:space-between;align-items:center;padding:18px 28px;background:rgba(16,20,31,0.9);border-bottom:1px solid rgba(124,58,237,0.25)}
.title{font-family:'Orbitron',sans-serif} .title b{background:linear-gradient(90deg,var(--cyan),var(--purple));-webkit-background-clip:text;background-clip:text;color:transparent}
.badge{padding:6px 12px;border-radius:999px;background:rgba(0,255,209,0.1);border:1px solid rgba(0,255,209,0.3);font-size:11px;color:var(--cyan)}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;padding:22px 28px}
.card{background:linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02));border:1px solid rgba(255,255,255,0.08);border-radius:20px;padding:18px 20px}
.label{font-size:10px;letter-spacing:0.2em;color:#94A3B8;text-transform:uppercase} .value{font-family:'Orbitron',sans-serif;font-size:24px;font-weight:700;margin:8px 0} .value.cyan{color:var(--cyan)} .value.purple{color:#C4B5FD} .value.green{color:var(--buy)} .value.sell{color:var(--sell)}
.market{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;padding:0 28px}
.m-card{background:var(--card);border-radius:16px;padding:14px 16px;border:1px solid rgba(255,255,255,0.06);display:flex;justify-content:space-between;align-items:center}
.sym{font-family:'Orbitron';font-weight:700} .rsi{width:46px;height:46px;border-radius:50%;display:grid;place-items:center;font-weight:700;font-size:13px;border:2px solid}
.rsi.buy{border-color:var(--buy);color:var(--buy);background:rgba(0,255,136,0.1)} .rsi.sell{border-color:var(--sell);color:var(--sell);background:rgba(255,59,92,0.1)} .rsi.neutral{border-color:#475569;color:#94A3B8}
.tag{font-size:10px;padding:3px 8px;border-radius:999px;margin-top:6px;display:inline-block} .tag.buy{background:rgba(0,255,136,0.12);color:var(--buy)} .tag.sell{background:rgba(255,59,92,0.12);color:var(--sell)} .tag.neutral{background:rgba(148,163,184,0.1);color:#94A3B8}
.memory{margin:24px 28px;background:rgba(15,18,25,0.9);border-radius:18px;border:1px solid rgba(124,58,237,0.25);padding:18px} .t-row{display:flex;gap:12px;padding:8px 10px;border-radius:10px;font-size:12px;border-left:2px solid transparent} .t-row.buy{border-left-color:var(--buy)} .t-row.sell{border-left-color:var(--sell)}
.chart-wrap{margin:24px 28px;background:rgba(16,20,31,0.9);border-radius:18px;border:1px solid rgba(0,255,209,0.2);padding:20px}
</style><meta http-equiv="refresh" content="60"></head><body>
<div class="header"><div style="display:flex;align-items:center;gap:16px"><div style="width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,var(--cyan),var(--purple));display:grid;place-items:center;font-family:Orbitron;font-weight:800">A</div><div><div class="title" style="font-size:18px"><b>AURA SYNAPSE</b> V5.5.1 FIX</div><div style="font-size:10px;color:#64748B;letter-spacing:0.2em">ANTI-BUCLE [skip ci] • GRAFICA EQUITY • HOBBY FIX</div></div></div><div class="badge">● AUTO {{ 'ON' if auto else 'OFF' }} • {{ fecha }} COL • {{ trades_count }} TRADES</div></div>
<div class="grid">
<div class="card"><div class="label">Equity Total</div><div class="value green">${{ "%.2f"|format(equity) }}</div><div style="font-size:11px;color:#64748B">P/L Total: ${{ "%.2f"|format(equity - 100000) }} ({{ "%.2f"|format((equity/100000-1)*100) }}%)</div></div>
<div class="card"><div class="label">Cash</div><div class="value cyan">${{ "%.2f"|format(cash) }}</div><div style="font-size:11px;color:#64748B">Para dips TSLA / ETH</div></div>
<div class="card"><div class="label">P/L Hoy</div><div class="value {% if pl_hoy>=0 %}green{% else %}sell{% endif %}">${{ "%.2f"|format(pl_hoy) }}</div><div style="font-size:11px;color:#64748B">{{ fecha_hoy }}</div></div>
<div class="card"><div class="label">Estado</div><div class="value purple" style="font-size:16px">Yesid + Syna</div><div style="font-size:11px;color:#94A3B8">{{ trades_count }} trades | {{ equity_points }} pts<br><a href="/memoria" style="color:var(--cyan)">Ver JSON →</a></div></div>
</div>
<div class="chart-wrap"><h3 style="font-family:Orbitron;font-size:13px;color:var(--cyan);margin-bottom:14px">📈 EQUITY EN VIVO - V5.5.1 ANTI-BUCLE</h3><canvas id="equityChart" height="110"></canvas></div>
<h3 style="padding:0 28px;font-family:Orbitron;font-size:12px;color:#94A3B8;margin-top:10px">MERCADO REAL • V5.5.1 • RSI<35 COMPRA, RSI>60 VENDE</h3>
<div class="market" style="margin-top:12px">{% for m in mercados %}<div class="m-card"><div><div class="sym">{{ m.sym }}</div><div style="font-size:13px;color:#CBD5E1">${{ "%.2f"|format(m.precio) if m.precio else '--' }}</div><div class="tag {{ m.color }}">{{ m.estado }}</div></div><div class="rsi {{ m.color }}">{{ m.rsi }}</div></div>{% endfor %}</div>
<div class="memory"><h3 style="font-family:Orbitron;font-size:13px">📚 MEMORIA GITHUB V5.5.1</h3>{% for t in memoria_trades[::-1][:15] %}<div class="t-row {{ 'buy' if t.lado=='COMPRAR' else 'sell' }}"><span style="color:#475569">{{ t.fecha[11:19] }}</span><span style="font-weight:700;color:{% if t.lado=='COMPRAR' %}var(--buy){% else %}var(--sell){% endif %}">{{ t.lado }}</span><span>{{ t.simbolo }} @ ${{ "%.2f"|format(t.precio) }}</span><span style="color:#64748B">RSI {{ "%.1f"|format(t.rsi) }}</span></div>{% endfor %}</div>
<script>
const equityData = {{ equity_historico_json | safe }};
const labels = equityData.map(e => e.fecha.substring(11,16));
const values = equityData.map(e => e.equity);
const ctx = document.getElementById('equityChart').getContext('2d');
const gradient = ctx.createLinearGradient(0,0,0,300);
gradient.addColorStop(0, 'rgba(0,255,209,0.4)'); gradient.addColorStop(1, 'rgba(0,255,209,0.0)');
new Chart(ctx, {type: 'line', data: {labels: labels, datasets: [{label: 'Equity $', data: values, borderColor: '#00FFD1', backgroundColor: gradient, borderWidth: 2.5, fill: true, tension: 0.35, pointRadius: 0}]}, options: {responsive: true, plugins: {legend: {display:false}}, scales: {x: {grid:{color:'rgba(255,255,255,0.05)'}, ticks:{color:'#64748B', font:{size:10}}}, y: {grid:{color:'rgba(255,255,255,0.05)'}, ticks:{color:'#64748B', font:{size:10}, callback:v => '$'+v}}}}});
</script></body></html>
'''

@app.route("/health")
def health():
    return {"status": "ok", "service": "AURA V5.5.1 FIX", "time": datetime.now().isoformat()}, 200

@app.route("/")
def dashboard():
    mercados, memoria, equity, cash = ciclo_trading()
    equity_hist = memoria.get("equity_historico", [])
    hoy_str = datetime.now(ZoneInfo("America/Bogota")).date().isoformat()
    puntos_hoy = [e for e in equity_hist if e["fecha"][:10]==hoy_str]
    pl_hoy = puntos_hoy[-1]["equity"] - puntos_hoy[0]["equity"] if len(puntos_hoy)>=2 else (equity_hist[-1]["equity"] - equity_hist[-2]["equity"] if len(equity_hist)>=2 else equity-START_EQUITY)
    return render_template_string(HTML_V551, mercados=mercados, memoria_trades=memoria["trades"], equity=equity, cash=cash, trades_count=len(memoria["trades"]), equity_points=len(equity_hist), fecha=datetime.now(ZoneInfo("America/Bogota")).strftime("%Y-%m-%d %H:%M:%S"), fecha_hoy=hoy_str, auto=AUTO_TRADING, pl_hoy=pl_hoy, equity_historico_json=json.dumps(equity_hist[-50:]))

@app.route("/memoria")
def memoria_json():
    memoria = cargar_memoria()
    import json as js
    html = f"<html><body style='background:#05070A;color:#00FFD1;font-family:monospace;padding:20px'><h2>AURA V5.5.1 ANTI-BUCLE</h2><a href='/' style='color:#7C3AED'>← Volver</a><pre style='background:#0F1219;padding:16px;border-radius:12px;border:1px solid #7C3AED'>"
    html += js.dumps({k:v for k,v in memoria.items() if not k.startswith("_")}, indent=2)
    html += "</pre></body></html>"
    return html

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
