"""
AURA_SYNAPSE V3.5 - DASHBOARD BONITO - RENDER - YESID + SYNA
Dashboard tipo Bloomberg / NASA - 24/7 gratis
"""
import json, os, time, threading
from datetime import datetime
import pytz
from flask import Flask, jsonify, render_template_string

try:
    from alpaca.trading.client import TradingClient
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    ALPACA_OK = True
except Exception as e:
    print(f"Alpaca import error: {e}")
    ALPACA_OK = False

app = Flask(__name__)

SYMBOLS = ["AAPL", "TSLA", "NVDA", "SPY"]
estado = {
    "status": "Iniciando...",
    "equity": 100146.55,
    "cash": 81207.15,
    "ultimo_analisis": "",
    "log": [],
    "detalles": {},  # {symbol: {precio, rsi, decision}}
    "vive_desde": datetime.now().isoformat(),
    "trades_hoy": 0
}

def get_time():
    return datetime.now(pytz.timezone('America/Bogota'))

def calc_rsi(prices, period=14):
    if len(prices) < period+1: return 50
    gains = []; losses = []
    for i in range(1, len(prices)):
        d = prices[i]-prices[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    if len(gains) < period: return 50
    avg_gain = sum(gains[-period:])/period
    avg_loss = sum(losses[-period:])/period
    if avg_loss == 0: return 100
    rs = avg_gain/avg_loss
    return 100 - (100/(1+rs))

def load_alpaca():
    api_key = os.environ.get('ALPACA_API_KEY','').strip()
    api_secret = os.environ.get('ALPACA_SECRET_KEY','').strip()
    if not api_key:
        estado["status"] = "Esperando Keys"
        return None, None
    try:
        trading = TradingClient(api_key, api_secret, paper=True)
        data_client = StockHistoricalDataClient(api_key, api_secret)
        acc = trading.get_account()
        estado["equity"] = float(acc.equity)
        estado["cash"] = float(acc.cash)
        estado["status"] = "Activa"
        print(f"✅ ALPACA-PY CONECTADA: {acc.status} Equity ${float(acc.equity):.2f}")
        return trading, data_client
    except Exception as e:
        print(f"❌ Error Alpaca: {e}")
        estado["status"] = f"Error: {e}"
        return None, None

def bot_loop():
    trading, data_client = load_alpaca()
    while True:
        try:
            if not trading:
                trading, data_client = load_alpaca()
                if not trading:
                    time.sleep(60); continue
            bog = get_time()
            acc = trading.get_account()
            estado["equity"] = float(acc.equity)
            estado["cash"] = float(acc.cash)
            estado["ultimo_analisis"] = bog.strftime('%Y-%m-%d %H:%M:%S COL')
            
            detalles = {}
            logs = []
            for sym in SYMBOLS:
                try:
                    req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Minute, limit=100)
                    bars = data_client.get_stock_bars(req).df
                    if sym in bars.index.get_level_values(0):
                        df = bars.loc[sym]
                    else:
                        df = bars
                    if len(df) < 20: continue
                    closes = df['close'].tolist()
                    precio = closes[-1]
                    rsi = calc_rsi(closes)
                    if rsi < 30: dec = "COMPRAR FUERTE"; color="#00ff88"
                    elif rsi < 45: dec = "COMPRAR"; color="#00cc66"
                    elif rsi > 75: dec = "VENDER FUERTE"; color="#ff4444"
                    elif rsi > 60: dec = "VENDER"; color="#ffaa00"
                    else: dec = "NEUTRAL"; color="#888"
                    
                    detalles[sym] = {"precio": precio, "rsi": round(rsi,1), "decision": dec, "color": color}
                    logs.append(f"{sym} ${precio:.2f} RSI {rsi:.1f} -> {dec}")
                except Exception as e:
                    print(f"{sym} err: {e}")
            estado["detalles"] = detalles
            estado["log"] = logs
            print(f"⏳ SYNA V3.5 - Equity ${estado['equity']:.2f} - {estado['ultimo_analisis']}")
            time.sleep(180)
        except Exception as e:
            print(f"Loop err: {e}"); time.sleep(60)

threading.Thread(target=bot_loop, daemon=True).start()

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AURA SYNAPSE V3.5 - Yesid + Syna</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Space+Grotesk:wght@400;700&display=swap');
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0f;color:#e0e0e0;font-family:'Space Grotesk',sans-serif;min-height:100vh;padding:20px}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:30px;border-bottom:1px solid #222;padding-bottom:20px}
.logo{font-size:28px;font-weight:700;background:linear-gradient(90deg,#00ff88,#00aaff);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.status-dot{display:inline-block;width:10px;height:10px;background:#00ff88;border-radius:50%;box-shadow:0 0 10px #00ff88;animation:pulse 2s infinite;margin-right:8px}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.5}}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px;margin-bottom:30px}
.card{background:#15151f;border:1px solid #222;border-radius:16px;padding:20px;position:relative;overflow:hidden}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,#00ff88,transparent);opacity:0.5}
.card-label{font-size:12px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}
.card-value{font-family:'JetBrains Mono',monospace;font-size:32px;font-weight:700}
.card-sub{font-size:12px;color:#666;margin-top:6px}
.grid-symbols{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin-bottom:30px}
.symbol-card{background:#15151f;border:1px solid #222;border-radius:12px;padding:18px;display:flex;justify-content:space-between;align-items:center}
.symbol-name{font-family:'JetBrains Mono',monospace;font-size:20px;font-weight:700}
.symbol-price{font-family:'JetBrains Mono',monospace;font-size:18px;color:#fff}
.rsi-badge{padding:6px 12px;border-radius:20px;font-size:12px;font-weight:700;font-family:'JetBrains Mono',monospace}
.log{background:#0f0f18;border:1px solid #222;border-radius:12px;padding:20px;font-family:'JetBrains Mono',monospace;font-size:13px;min-height:120px}
.log-line{padding:4px 0;border-bottom:1px solid #1a1a25;color:#aaa}
.footer{margin-top:40px;text-align:center;color:#444;font-size:11px;letter-spacing:1px}
.live{color:#00ff88}
a{color:#00aaff;text-decoration:none}
</style>
<script>
setInterval(()=>{fetch('/api').then(r=>r.json()).then(d=>{
 document.getElementById('equity').innerText='$'+d.equity.toLocaleString();
 document.getElementById('cash').innerText='$'+d.cash.toLocaleString();
 document.getElementById('status').innerText=d.status;
 document.getElementById('time').innerText=d.ultimo_analisis;
 let html='';
 for(let s of d.simbolos){
   let det = d.detalles[s] || {precio:'...',rsi:'-',decision:'CARGANDO',color:'#888'};
   html+=`<div class="symbol-card">
     <div><div class="symbol-name">${s}</div><div style="font-size:11px;color:#666">RSI ${det.rsi}</div></div>
     <div style="text-align:right"><div class="symbol-price">$${det.precio}</div><div class="rsi-badge" style="background:${det.color}20;color:${det.color};border:1px solid ${det.color}40">${det.decision}</div></div>
   </div>`;
 }
 document.getElementById('symbols').innerHTML=html;
 let logHtml='';
 (d.log||[]).forEach(l=>{logHtml+=`<div class="log-line">${l}</div>`});
 if(logHtml==='') logHtml='<div style="color:#555">Esperando apertura mercado NYSE (8:30 AM COL)...</div>';
 document.getElementById('log').innerHTML=logHtml;
});}, 5000);
</script>
</head>
<body>
<div class="header">
 <div class="logo">🧠 AURA SYNAPSE V3.5</div>
 <div style="font-family:'JetBrains Mono',monospace;font-size:13px"><span class="status-dot"></span><span id="status" class="live">Activa</span> • <span id="time">...</span></div>
</div>

<div class="cards">
 <div class="card"><div class="card-label">Equity Total (Paper)</div><div class="card-value" id="equity">$100,146</div><div class="card-sub">Alpaca Markets • Paper Trading • Live</div></div>
 <div class="card"><div class="card-label">Cash Disponible</div><div class="card-value" id="cash" style="color:#00aaff">$81,207</div><div class="card-sub">Para nuevas posiciones</div></div>
 <div class="card"><div class="card-label">Creadores</div><div class="card-value" style="font-size:18px">Yesid + Syna</div><div class="card-sub">Madrid, COL → Render 24/7 • <a href="/api">API JSON</a> • <a href="/health">Health</a></div></div>
</div>

<h3 style="margin-bottom:12px;color:#888;font-size:13px;letter-spacing:1px;text-transform:uppercase">Mercado en Tiempo Real - RSI</h3>
<div id="symbols" class="grid-symbols"><div style="color:#555">Cargando símbolos...</div></div>

<h3 style="margin-bottom:12px;color:#888;font-size:13px;letter-spacing:1px;text-transform:uppercase">Log de Syna</h3>
<div id="log" class="log">Iniciando cerebro...</div>

<div class="footer">SYNAPSE V3.5 • RENDER FREE • PAPER TRADING • NO ES ASESORÍA FINANCIERA • HECHO CON ❤️ EN MADRID, CUNDINAMARCA<br>Vive desde: {{vive_desde}} • URL: https://aura-synapse-yesid.onrender.com</div>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(DASHBOARD_HTML, vive_desde=estado["vive_desde"])

@app.route('/api')
def api():
    return jsonify({
        "mensaje": "🧠 SYNA VIVE 24/7 EN RENDER V3.5",
        "status": estado.get("status"),
        "equity": estado.get("equity"),
        "cash": estado.get("cash"),
        "ultimo_analisis": estado.get("ultimo_analisis"),
        "log": estado.get("log", []),
        "detalles": estado.get("detalles", {}),
        "vive_desde": estado.get("vive_desde"),
        "simbolos": SYMBOLS,
        "paper": True
    })

@app.route('/health')
def health():
    return "OK SYNA VIVE V3.5", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
